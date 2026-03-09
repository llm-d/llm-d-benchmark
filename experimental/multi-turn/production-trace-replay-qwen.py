import asyncio
import json
import logging
import sys
import os
import argparse
import functools
import numpy as np

# Add local inference-perf-ref to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "inference-perf-ref"))
from dataclasses import dataclass
from typing import Generator, List, Optional, Tuple, Dict, Any
from pydantic import ConfigDict, Field

from inference_perf.apis.base import InferenceAPIData, LazyLoadInferenceAPIData
from inference_perf.apis.completion import CompletionAPIData
from inference_perf.apis.user_session import LocalUserSession, UserSessionCompletionAPIData
from inference_perf.apis.base import InferenceAPIData, LazyLoadInferenceAPIData, InferenceInfo
from inference_perf.client.modelserver.vllm_client import vLLMModelServerClient
from inference_perf.client.requestdatacollector.multiprocess import MultiprocessRequestDataCollector
from aiohttp import ClientResponse
import random
import time
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
    StorageConfigBase,
)
from inference_perf.client.filestorage import LocalStorageClient
from inference_perf.datagen.base import DataGenerator, LazyLoadDataMixin
from inference_perf.loadgen.load_generator import LoadGenerator
from inference_perf.loadgen.load_timer import LoadTimer
from inference_perf.utils.custom_tokenizer import CustomTokenizer
from inference_perf.reportgen.base import ReportGenerator
from inference_perf.client.metricsclient.base import StageRuntimeInfo
from inference_perf.client.metricsclient import PerfRuntimeParameters

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


class TokenBasedLocalUserSession:
    user_session_id: str
    contexts: List[int]

    def __init__(self, user_session_id: str, context: List[int] = None):
        self.user_session_id = user_session_id
        self.contexts = context if context else []
        self._current_round = 0
        self._in_flight = None
        self._waiting_rounds = None

    @property
    def in_flight(self) -> asyncio.Lock:
        if self._in_flight is None:
            self._in_flight = asyncio.Lock()
        return self._in_flight

    @property
    def waiting_rounds(self) -> asyncio.Queue:
        if self._waiting_rounds is None:
            self._waiting_rounds = asyncio.Queue()
        return self._waiting_rounds

    def __getstate__(self):
        state = self.__dict__.copy()
        state["_in_flight"] = None
        state["_waiting_rounds"] = None
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        # Primitives will be initialized lazily on first access

    async def get_context(self, round: int) -> List[int]:
        if not self.waiting_rounds.empty() or self.in_flight.locked():
            # entering waiting queue
            future: asyncio.Future[bool] = asyncio.Future()
            self.waiting_rounds.put_nowait(future)
            await future
        await self.in_flight.acquire()
        self._current_round += 1
        return self.contexts

    def update_context(self, response: List[int]) -> None:
        self.contexts = response

        if not self.waiting_rounds.empty():
            future = self.waiting_rounds.get_nowait()
            future.set_result(True)

        self.in_flight.release()


class UserSessionTokenCompletionAPIData(CompletionAPIData):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    user_session: TokenBasedLocalUserSession = Field(exclude=True)
    target_round: int
    prompt_token_ids: Optional[List[int]] = None
    _session_context: List[int] = []
    # These fields are in CompletionAPIData, but we need to ensure they are populated
    max_tokens: int = 0
    ignore_eos: bool = True

    async def to_payload(
        self, effective_model_name: str, max_tokens: int, ignore_eos: bool, streaming: bool
    ) -> Dict[str, Any]:
        self._session_context = await self.user_session.get_context(self.target_round)
        # Concatenate session context (list) and current prompt (list)
        # We assume self.prompt_token_ids is populated
        full_prompt = self._session_context + (self.prompt_token_ids or [])
        
        if self.max_tokens == 0:
            self.max_tokens = max_tokens
        
        # Override ignore_eos if set in self
        ignore_eos = self.ignore_eos
            
        return {
            "model": effective_model_name,
            "prompt": full_prompt, # vLLM supports list of ints
            "max_tokens": self.max_tokens,
            "ignore_eos": ignore_eos,
            "stream": streaming,
            **({"stream_options": {"include_usage": True}} if streaming else {}),
        }

    def update_inference_info(self, inference_info: InferenceInfo) -> None:
        inference_info.extra_info["user_session"] = self.user_session.user_session_id
        inference_info.extra_info["chat_round"] = self.user_session._current_round

    async def process_response(
        self, response: ClientResponse, config: APIConfig, tokenizer: CustomTokenizer, lora_adapter: Optional[str] = None
    ) -> InferenceInfo:
        # We need to manually calculate input_tokens because we sent token IDs and
        # the default implementation might rely on tokenizer.count_tokens(self.prompt) where self.prompt is empty.
        
        # Capture start time for manual TTFT/TPOT if needed, but the base implementation does streaming parsing
        # We'll call super() to handle streaming parsing, but we might need to patch input_tokens afterwards
        
        inference_info = await super().process_response(response, config, tokenizer, lora_adapter)
        self.update_inference_info(inference_info)
        
        # Calculate input tokens ensuring we account for the full context if that's what was sent
        # The prompt was: self._session_context + (self.prompt_token_ids or [])
        full_context_len = len(self._session_context) + len(self.prompt_token_ids or [])
        inference_info.input_tokens = full_context_len

        # Tokenize response to append to context
        output_ids = tokenizer.tokenizer.encode(self.model_response)
        full_context = self._session_context + (self.prompt_token_ids or []) + output_ids
        self.user_session.update_context(full_context)
        
        return inference_info

    async def process_failure(
        self,
        response: Optional[ClientResponse],
        config: APIConfig,
        tokenizer: CustomTokenizer,
        exception: Exception,
        lora_adapter: Optional[str] = None,
    ) -> Optional[InferenceInfo]:
        inference_info = InferenceInfo()
        self.update_inference_info(inference_info)
        # On failure, reverting to session context (state before this turn)
        self.user_session.update_context(self._session_context)
        return inference_info


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
        self.user_sessions: Dict[str, TokenBasedLocalUserSession] = {}
        # Stores (chat_id, turn_index_in_trace) -> entry mapping or similar if needed
        # Actually load_lazy_data needs to access entry by index
        self._load_trace(trace_file)

    @functools.lru_cache(maxsize=10000)
    def _hash_to_tokens(self, hash_id: int) -> List[int]:
        # Deterministically map hash_id to 16 tokens
        # Using numpy.random.default_rng for better performance in batch generation
        rng = np.random.default_rng(hash_id)
        return rng.integers(100, 32000, size=16).tolist()

    def _block_ids_to_tokens(self, block_ids: List[int]) -> List[int]:
        tokens = []
        for bid in block_ids:
            tokens.extend(self._hash_to_tokens(bid))
        return tokens

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

            turns_per_session_map = {}
            prompt_lengths = []
            output_lengths = []

            for i, data in enumerate(raw_entries):
                hash_ids = data.get("hash_ids", [])
                
                # Convert hash_ids to input_ids (tokens)
                input_ids = self._block_ids_to_tokens(hash_ids)
                
                chat_id = int(data.get("chat_id", -1))
                parent_chat_id = int(data.get("parent_chat_id", -1))
                
                # Determine correct session ID
                session_id = str(chat_id) if parent_chat_id == -1 else str(parent_chat_id)
                
                if session_id not in self.user_sessions:
                    self.user_sessions[session_id] = TokenBasedLocalUserSession(user_session_id=session_id)
                
                turns_per_session_map[session_id] = turns_per_session_map.get(session_id, 0) + 1
                prompt_lengths.append(len(input_ids))
                output_lengths.append(int(data.get("output_length", 10)))

                self.trace_entries.append(
                    TraceEntry(
                        timestamp=float(data.get("timestamp", 0.0)),
                        input_ids=input_ids,
                        input_length=len(input_ids), # Use actual token length
                        output_length=output_lengths[-1],
                        chat_id=chat_id,
                        parent_chat_id=parent_chat_id,
                        turn=int(data.get("turn", 1)), # explicit turn number if available
                    )
                )

            logger.info(f"Loaded {len(self.trace_entries)} trace entries across {len(self.user_sessions)} sessions")
            
            # Additional Trace Statistics
            turns_per_session = list(turns_per_session_map.values())
            if turns_per_session:
                logger.info("--- Trace Session Statistics ---")
                logger.info(f"Turns per Session: min={np.min(turns_per_session)}, p50={np.percentile(turns_per_session, 50):.1f}, p90={np.percentile(turns_per_session, 90):.1f}, max={np.max(turns_per_session)}")
                logger.info(f"Prompt Lengths:    min={np.min(prompt_lengths)}, p50={np.percentile(prompt_lengths, 50):.1f}, p90={np.percentile(prompt_lengths, 90):.1f}, max={np.max(prompt_lengths)}")
                logger.info(f"Output Lengths:    min={np.min(output_lengths)}, p50={np.percentile(output_lengths, 50):.1f}, p90={np.percentile(output_lengths, 90):.1f}, max={np.max(output_lengths)}")
                logger.info("--------------------------------")
        except Exception as e:
            logger.error(f"Failed to load trace file: {e}")
            raise
    
    def get_data(self) -> Generator[InferenceAPIData, None, None]:
        # Yield LazyLoadInferenceAPIData for each entry
        for i, entry in enumerate(self.trace_entries):
            session_id = str(entry.chat_id) if entry.parent_chat_id == -1 else str(entry.parent_chat_id)
            prefered_worker_id = hash(session_id)
            yield LazyLoadInferenceAPIData(data_index=i, prefered_worker_id=prefered_worker_id)
    
    def load_lazy_data(self, data: LazyLoadInferenceAPIData) -> InferenceAPIData:
        entry = self.trace_entries[data.data_index]
        session_id = str(entry.chat_id) if entry.parent_chat_id == -1 else str(entry.parent_chat_id)
        
        target_round = entry.turn - 1
        
        return UserSessionTokenCompletionAPIData(
            prompt_token_ids=entry.input_ids,
            prompt="", # Token IDs take precedence (or dealt with in to_payload)
            max_tokens=entry.output_length,
            ignore_eos=True,
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
    parser = argparse.ArgumentParser(description="Multi-turn Trace Replay Benchmark")
    parser.add_argument("--model-name", type=str, default=os.environ.get("MODEL_NAME", "google/gemma-3-1b-it"), help="Model name")
    parser.add_argument("--base-url", type=str, default=os.environ.get("ENDPOINT_BASE_URL", "http://localhost:8000"), help="Base URL of the inference server")
    parser.add_argument("--limit", type=int, default=1000, help="Limit the number of trace entries to replay")
    
    args = parser.parse_args()

    # Environment variables or defaults for server
    model_name = args.model_name
    base_url = args.base_url
    limit = args.limit
    
    trace_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "qwen_traceA_blksz_16.jsonl"
    )
    
    logger.info(f"Starting Multi-turn Benchmark")
    logger.info(f"Model: {model_name}")
    logger.info(f"Base URL: {base_url}")
    logger.info(f"Limit: {limit}")
    
    # configs
    api_config = APIConfig(type=APIType.Completion, streaming=True)
    load_config = LoadConfig(
        type=LoadType.CONSTANT,
        stages=[StandardLoadStage(duration=3600, rate=1.0)],
        num_workers=os.cpu_count() or 1
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
        limit=limit
    )
    
    loadgen = TraceLoadGenerator(datagen=datagen, load_config=load_config)
    
    # Instantiate client with explicit args
    metrics_collector = MultiprocessRequestDataCollector()
    
    # Mock Client for Verification
    from custom_mock_client import CustomMockClient

    client = CustomMockClient(
        metrics_collector=metrics_collector,
        api_config=api_config,
        mock_latency=0.01
    )
    
    logger.info("Starting load generation...")
    async with metrics_collector.start():
        await loadgen.run(client)
    
    logger.info("Benchmark finished. Generating Report...")
    
    # Generate Report
    
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
            per_request=True,
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

    # Identify output directory
    output_dir = os.path.join(os.getcwd(), "reports")
    
    # Save reports to local storage
    storage_config = StorageConfigBase(path=output_dir)
    storage_client = LocalStorageClient(config=storage_config)
    
    # Save reports
    storage_client.save_report(reports)
    logger.info(f"Reports saved to: {output_dir}")

    # Generate and print TTFT by Turn Buckets
    generate_ttft_report_by_turns(metrics_collector.get_metrics())

def generate_ttft_report_by_turns(metrics: List[Any]):
    """
    Generates and prints a report of TTFT percentiles grouped by turn number buckets.
    """
    
    # Define buckets: (min_turn, max_turn, label)
    # max_turn is inclusive. None means infinity.
    buckets = [
        (1, 1, "Turn 1"),
        (2, 5, "Turns 2-5"),
        (6, 10, "Turns 6-10"),
        (11, None, "Turns 11+"),
    ]
    
    bucket_data = {label: [] for _, _, label in buckets}
    
    logger.info("Processing metrics for TTFT by Turn Buckets...")
    
    for metric in metrics:
        # Skip failed requests or those without TTFT info
        if metric.error is not None:
            continue
            
        # TTFT is the time to receive the first token. 
        # In InferenceInfo, output_token_times[0] is the timestamp of the first token.
        # TTFT = output_token_times[0] - start_time
        if not metric.info.output_token_times:
            continue
            
        ttft = metric.info.output_token_times[0] - metric.start_time
        
        # Get turn number
        # extra_info["chat_round"] was populated with _current_round
        # Logic in TokenBasedLocalUserSession: _current_round starts at 0, increments to 1 on first get_context call
        # So it is 1-based.
        turn = metric.info.extra_info.get("chat_round", 0)
        
        if turn == 0:
            # Fallback if chat_round is missing or 0 (should correspond to pre-fill or error state if not set)
            # However, our logic sets it.
            continue
            
        # Assign to bucket
        for min_t, max_t, label in buckets:
            if turn >= min_t and (max_t is None or turn <= max_t):
                bucket_data[label].append(ttft)
                break
    
    logger.info("\n=== TTFT by Turn Buckets (seconds) ===")
    logger.info(f"{'Bucket':<15} | {'Count':<5} | {'P50':<8} | {'P90':<8} | {'P99':<8}")
    logger.info("-" * 55)
    
    for _, _, label in buckets:
        data = bucket_data[label]
        count = len(data)
        if count > 0:
            p50 = np.percentile(data, 50)
            p90 = np.percentile(data, 90)
            p99 = np.percentile(data, 99)
            logger.info(f"{label:<15} | {count:<5} | {p50:<8.4f} | {p90:<8.4f} | {p99:<8.4f}")
        else:
            logger.info(f"{label:<15} | {count:<5} | {'N/A':<8} | {'N/A':<8} | {'N/A':<8}")
    logger.info("======================================\n")

if __name__ == "__main__":
    asyncio.run(main())

