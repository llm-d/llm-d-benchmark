import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Generator, List, Optional, Tuple, Dict, Any

from inference_perf.apis.base import InferenceAPIData, LazyLoadInferenceAPIData
from inference_perf.apis.completion import CompletionAPIData
from inference_perf.apis.user_session import LocalUserSession, UserSessionCompletionAPIData
from inference_perf.client.modelserver.vllm_client import vLLMModelServerClient
from inference_perf.client.requestdatacollector.local import LocalRequestDataCollector
from inference_perf.config import (
    APIConfig,
    APIType,
    DataConfig,
    LoadConfig,
    StandardLoadStage,
    ModelServerClientConfig,
    ModelServerType,
    CustomTokenizerConfig,
    LoadType,
    ReportConfig,
    RequestLifecycleMetricsReportConfig,
    TraceConfig,
    TraceFormat,
    Config,
)
from inference_perf.datagen.base import DataGenerator, LazyLoadDataMixin
from inference_perf.loadgen.load_generator import LoadGenerator
from inference_perf.loadgen.load_timer import LoadTimer
from inference_perf.utils.custom_tokenizer import CustomTokenizer
from inference_perf.reportgen.base import ReportGenerator
from inference_perf.client.metricsclient.base import StageRuntimeInfo

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class TraceEntry:
    timestamp: float
    input_ids: List[int]
    input_length: int
    output_length: int
    chat_id: int
    parent_chat_id: int
    turn: int


class TraceDataGenerator(DataGenerator, LazyLoadDataMixin):
    def __init__(
        self,
        api_config: APIConfig,
        config: DataConfig,
        trace_file: str,
        tokenizer: Optional[CustomTokenizer] = None,
        limit: int = 0, # 0 means no limit
    ):
        super().__init__(api_config, config, tokenizer)
        self.limit = limit
        self.trace_entries: List[TraceEntry] = []
        self.user_sessions: Dict[str, LocalUserSession] = {}
        # Stores (chat_id, turn_index_in_trace) -> entry mapping or similar if needed
        # Actually load_lazy_data needs to access entry by index
        self._load_trace(trace_file)

    def _load_trace(self, trace_file: str):
        logger.info(f"Loading trace from {trace_file}")
        try:
            with open(trace_file, "r") as f:
                raw_entries = []
                for line in f:
                    data = json.loads(line)
                    raw_entries.append(data)

            # Sort by timestamp
            raw_entries.sort(key=lambda x: float(x.get("timestamp", 0.0)))

            # Apply limit
            if self.limit > 0:
                logger.info(f"Limiting trace to first {self.limit} requests")
                raw_entries = raw_entries[:self.limit]

            for i, data in enumerate(raw_entries):
                input_ids = data.get("hash_ids", [])
                chat_id = int(data.get("chat_id", -1))
                parent_chat_id = int(data.get("parent_chat_id", -1))
                
                # Determine correct session ID
                # If parent_chat_id is -1, this is a new session (or single turn)
                # If parent_chat_id is set, it belongs to that session
                # In this specific trace format, let's assume if parent_chat_id != -1, 
                # it's the session ID. If -1, chat_id is the session ID.
                session_id = str(chat_id) if parent_chat_id == -1 else str(parent_chat_id)
                
                if session_id not in self.user_sessions:
                    self.user_sessions[session_id] = LocalUserSession(user_session_id=session_id)

                self.trace_entries.append(
                    TraceEntry(
                        timestamp=float(data.get("timestamp", 0.0)),
                        input_ids=input_ids,
                        input_length=int(data.get("input_length", len(input_ids))),
                        output_length=int(data.get("output_length", 10)),
                        chat_id=chat_id,
                        parent_chat_id=parent_chat_id,
                        turn=int(data.get("turn", 1)), # explicit turn number if available
                    )
                )

            logger.info(f"Loaded {len(self.trace_entries)} trace entries across {len(self.user_sessions)} sessions")
        except Exception as e:
            logger.error(f"Failed to load trace file: {e}")
            raise

    def get_data(self) -> Generator[InferenceAPIData, None, None]:
        # Yield LazyLoadInferenceAPIData for each entry
        for i, entry in enumerate(self.trace_entries):
            # We want to assign the same worker to the same session_id to ensure
            # they are processed on the same worker if we were using multi-processing with strict affinity,
            # but inference-perf LoadGenerator uses prefered_worker_id to route requests.
            session_id = str(entry.chat_id) if entry.parent_chat_id == -1 else str(entry.parent_chat_id)
            # Use hash of session_id to pick a worker
            prefered_worker_id = hash(session_id)
            yield LazyLoadInferenceAPIData(data_index=i, prefered_worker_id=prefered_worker_id)
    
    def load_lazy_data(self, data: LazyLoadInferenceAPIData) -> InferenceAPIData:
        entry = self.trace_entries[data.data_index]
        session_id = str(entry.chat_id) if entry.parent_chat_id == -1 else str(entry.parent_chat_id)
        
        # We need to determine the 'target_round' for this specific request.
        # We can calculate it on the fly or pre-calculate it.
        # Since we are iterating sequentially in _load_trace, we could rely on order.
        # But 'turn' field in json might be useful.
        # However, LocalUserSession expects strict 0, 1, 2... indexing for rounds.
        # Let's trust the 'turn' field from JSON if it's 1-based, convert to 0-based.
        target_round = entry.turn - 1
        
        return UserSessionCompletionAPIData(
            prompt_token_ids=entry.input_ids,
            prompt="", # Token IDs take precedence
            sampling_params={
                "max_tokens": entry.output_length,
                "ignore_eos": True,
            },
            stream=True,
            user_session=self.user_sessions[session_id],
            target_round=target_round,
        )
    
    def get_request_count(self) -> int:
        return len(self.trace_entries)

    def get_supported_apis(self) -> List[APIType]:
        return [APIType.Completion]

    def is_io_distribution_supported(self) -> bool:
        return False

    def is_shared_prefix_supported(self) -> bool:
        return True
    
    def is_prefered_worker_requested(self) -> bool:
        return True


class TraceLoadTimer(LoadTimer):
    def __init__(self, timestamps: List[float]):
        super().__init__()
        self.timestamps = timestamps

    def start_timer(self, initial: Optional[float] = None) -> Generator[float, None, None]:
        if not self.timestamps:
            return
            
        start_time = initial if initial is not None else 0.0
        
        for ts in self.timestamps:
            yield start_time + ts


class TraceLoadGenerator(LoadGenerator):
    def __init__(
        self,
        datagen: TraceDataGenerator,
        load_config: LoadConfig,
    ):
        super().__init__(datagen, load_config)
        self.trace_datagen = datagen

    def get_timer(self, rate: float, duration: float) -> LoadTimer:
        timestamps = [entry.timestamp for entry in self.trace_datagen.trace_entries]
        return TraceLoadTimer(timestamps)


async def main():
    # Configuration
    trace_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "qwen_traceA_blksz_16.jsonl"
    )
    
    # Environment variables or defaults for server
    model_name = os.environ.get("LLMDBENCH_DEPLOY_CURRENT_MODEL", "google/gemma-3-1b-it")
    base_url = os.environ.get("LLMDBENCH_HARNESS_STACK_ENDPOINT_URL", "http://localhost:8000")
    
    logger.info(f"Starting Multi-turn Benchmark")
    logger.info(f"Model: {model_name}")
    logger.info(f"Base URL: {base_url}")
    
    # configs
    api_config = APIConfig(type=APIType.Completion, streaming=True)
    load_config = LoadConfig(
        type=LoadType.CONSTANT,
        stages=[StandardLoadStage(duration=3600, rate=1.0)],
        num_workers=1 # Force single worker for now if debugging, or strictly rely on affinity
    )
    # Note: load_config.num_workers will be effectively used by LoadGenerator.
    # If we want to use multiple workers, we need to ensure affinity works.
    # Our TraceDataGenerator sets is_prefered_worker_requested=True, so it should be fine.
    
    tokenizer_config = CustomTokenizerConfig(pretrained_model_name_or_path=model_name)
    data_config = DataConfig(
        type="synthetic",
        trace=TraceConfig(file=trace_file, format=TraceFormat.AZURE_PUBLIC_DATASET)
    ) # Placeholder

    # Initialize components
    tokenizer = None 
    
    datagen = TraceDataGenerator(
        api_config=api_config,
        config=data_config,
        trace_file=trace_file,
        tokenizer=tokenizer,
        limit=1000
    )
    
    loadgen = TraceLoadGenerator(datagen=datagen, load_config=load_config)
    
    # Instantiate client with explicit args
    metrics_collector = LocalRequestDataCollector()
    client = vLLMModelServerClient(
        metrics_collector=metrics_collector,
        api_config=api_config,
        uri=base_url,
        model_name=model_name,
        tokenizer_config=tokenizer_config,
        max_tcp_connections=100, 
        additional_filters=[],
        ignore_eos=True,
        api_key=None
    )
    
    logger.info("Starting load generation...")
    await loadgen.run(client)
    
    logger.info("Benchmark finished. Generating Report...")
    
    # Generate Report
    # Update stage info in runtime parameters for report generation
    # loadgen.stage_runtime_info is populated after run
    runtime_parameters = Any # Mocking it or reconstructing it?
    # Actually ReportGenerator expects PerfRuntimeParameters which contains stages
    from inference_perf.client.metricsclient import PerfRuntimeParameters
    
    runtime_params = PerfRuntimeParameters(
        start_time=0.0,
        duration=0.0,
        model_server_metrics={},
        stages=loadgen.stage_runtime_info,
    )
    
    report_config = ReportConfig(
        request_lifecycle=RequestLifecycleMetricsReportConfig(
            summary=True,
            per_stage=True,
            percentiles=[50, 90, 99],
        )
    )
    
    report_generator = ReportGenerator(
        metrics_client=None, # No prometheus metrics
        metrics_collector=metrics_collector,
        config=Config() # Empty config or pass relevant parts
    )
    
    reports = await report_generator.generate_reports(report_config, runtime_params)
    
    # Print Summary Report
    for report in reports:
        if report.name == "summary_lifecycle_metrics":
            logger.info("=== Lifecycle Metrics Summary ===")
            logger.info(json.dumps(report.contents, indent=2))
        elif report.name == "config":
            continue
        else:
            logger.info(f"Report: {report.name}")
            # logger.info(json.dumps(report.contents, indent=2))

if __name__ == "__main__":
    asyncio.run(main())

