# No-Kubernetes (`nok8s`) deployment

## Concept

The `nok8s` deployment method runs the llm-d routing stack — **vLLM + EPP
(router) + Envoy** — as plain `docker`/`podman` containers on a single host,
with **no Kubernetes cluster**. The benchmark harness (`llmdbenchmark run`)
also runs as a **local container** against the Envoy front door, so the entire
standup → run → teardown lifecycle is cluster-free.

```
client ──▶ Envoy :8081 ──ext_proc──▶ EPP :9002 ──▶ picks a worker
              │                        (reads endpoints.yaml, file-discovery)
              └──────────────────────▶ vLLM :8000  (OpenAI-compatible API)
```

Instead of watching a Kubernetes `InferencePool`, the EPP reads its worker
inventory from a YAML file via the file-discovery plugin. This is the harness
equivalent of the upstream
[llm-d no-Kubernetes guide](https://github.com/llm-d/llm-d/tree/main/guides/no-kubernetes-deployment).

Use it for HPC/Slurm nodes, bare-metal boxes, or a single GPU workstation
where standing up Kubernetes is not worth it.

The host running the containers does not have to be the host running
`llmdbenchmark`. Point `nok8s.connection` at a node and the whole lifecycle —
standup, smoketest, run, teardown — drives that node over SSH, with nothing to
install or configure there beyond a container runtime. See
[Remote host](#remote-host).

## Prerequisites

`llmdbenchmark standup` validates these automatically in step 00; a missing or
broken container runtime is fatal, the rest are warnings.

| Requirement | Notes |
|-------------|-------|
| Linux host + NVIDIA GPU(s) | Images/flags are NVIDIA + vLLM specific |
| NVIDIA driver | `nvidia-smi` must work |
| Container runtime | **docker** or **podman** (`nok8s.runtime`), on the host named by `nok8s.connection` |
| NVIDIA Container Toolkit | docker: `nvidia-ctk runtime configure --runtime=docker`; podman: `nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml` |
| Hugging Face token | `export HUGGING_FACE_HUB_TOKEN=hf_...` (only for gated models) |
| Free host ports | 8000 (vLLM), 8081 (Envoy), 9002/9003/9090 (EPP), 19000 (Envoy admin) |
| Outbound network | to pull images (docker.io, ghcr.io) and model weights (Hugging Face) |
| `llmdbenchmark` CLI | `./install.sh` (Python 3.11+) |

Every requirement above applies to the host that runs the containers. For a
remote `nok8s.connection` the client additionally needs `ssh` and `scp`, and the
node needs `curl`, `timeout` and a listening-socket tool (`ss` or `lsof`) —
step 00 checks each one on the side that has to have it.

**Not required:** Kubernetes, `kubectl`/`oc`, `helm`/`helmfile`, Gateway CRDs,
PVCs, or any cluster access. For a remote node, also not required: a
daemon listening on a TCP port, a manually opened SSH tunnel, an agent or
anything else installed there, or a login session — `llmdbenchmark` never needs
you to shell into the node.

Verify GPU-in-container before you start:
```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## Without a GPU

Serving a model needs the accelerator, but *validating the plumbing* does not.
Rendering and `--dry-run` never touch a device or a cluster, so a plain laptop
can still check that a nok8s scenario renders and that the container commands
are the ones you expect. Verified on macOS (Darwin 25.3, Python 3.13, `docker`
present and usable, no GPU, no cluster, no `helm`/`yq`/`kind`):

```bash
llmdbenchmark --spec config/specification/guides/nok8s.yaml.j2 --base-dir . plan
llmdbenchmark --spec config/specification/guides/nok8s.yaml.j2 --base-dir . --dry-run standup --methods nok8s
llmdbenchmark --spec config/specification/guides/nok8s.yaml.j2 --base-dir . --dry-run run
llmdbenchmark --spec config/specification/guides/nok8s.yaml.j2 --base-dir . --dry-run teardown --methods nok8s
```

| What | Without a GPU |
|------|---------------|
| `pytest tests/ -q -n2` | **Works** (1389 passed, 32 skipped). `tests/test_nok8s_plan.py` covers template rendering, per-accelerator device flags, per-replica pinning, and the preflight |
| `plan` | **Works** -- renders all 36 artifacts, including `31/32/33/34_nok8s-*` |
| `--dry-run standup --methods nok8s` | **Works** (12/12 steps) -- step 06 logs each `docker run` it *would* execute, and records `http://localhost:8081` |
| `--dry-run run` | **Works** -- endpoint resolves locally with no cluster query, profiles render, the harness `docker run` is logged |
| `--dry-run teardown --methods nok8s` | **Works** -- logs one `docker rm -f` per container |
| `teardown --methods nok8s` (live) | **Works** -- `docker rm -f` is idempotent, so it is safe with nothing running |
| `standup` (live) | **Fails at step 06.** It emits `docker run -d --name vllm-0 --gpus all ...`, which a GPU-less docker rejects: `could not select device driver "" with capabilities: [[gpu]]` |
| `run` / `smoketest` (live) | **Fails** -- nothing is serving the model |

Two caveats before you read a green dry-run as "my host is fine":

- **`--dry-run` skips the step 00 preflight.** It logs what it *would* verify
  and returns success. It proves the plan and the command strings, not the host.
- **Run live, step 00 passes anyway on a GPU-less host** with a working
  container runtime: only a missing or broken runtime is fatal, so the missing
  accelerator is a warning, not an error:
  ```
  WARNING  'nvidia-smi' found no nvidia accelerator; vLLM needs the device + driver present, ...
  WARNING  $HUGGING_FACE_HUB_TOKEN is not set; gated Hugging Face models will fail to download ...
  INFO     nok8s preflight passed (runtime=docker).
  ```
  The port-in-use check shells out to `ss -ltn`, so on a host without `ss`
  (macOS) it silently reports nothing rather than warning.

## Usage

```bash
export HUGGING_FACE_HUB_TOKEN=hf_...     # for gated models

# Bring up vLLM + EPP + Envoy as local containers (step 00 runs the preflight).
llmdbenchmark --spec config/specification/guides/nok8s.yaml.j2 --base-dir . standup --methods nok8s

# Probe the stack over HTTP (no cluster needed); standup does not chain this one.
llmdbenchmark --spec config/specification/guides/nok8s.yaml.j2 --base-dir . smoketest

# Benchmark it — the harness runs as a local container against http://localhost:8081.
llmdbenchmark --spec config/specification/guides/nok8s.yaml.j2 --base-dir . run

# Remove the containers.
llmdbenchmark --spec config/specification/guides/nok8s.yaml.j2 --base-dir . teardown --methods nok8s
```

`--methods nok8s` is optional when the scenario sets `nok8s.enabled: true`
(the method is auto-detected), but harmless to pass explicitly.

To run the same stack on a bare-metal node, add `--set nok8s.connection=<IP>`
to **each** of those commands — `run` included, since it launches the harness
on the node:

```bash
llmdbenchmark --spec guides/nok8s standup  --methods nok8s --set nok8s.connection=10.0.0.7
llmdbenchmark --spec guides/nok8s smoketest                --set nok8s.connection=10.0.0.7
llmdbenchmark --spec guides/nok8s run                      --set nok8s.connection=10.0.0.7
llmdbenchmark --spec guides/nok8s teardown --methods nok8s --set nok8s.connection=10.0.0.7
```

Or `export LLMDBENCH_SET='nok8s.connection=10.0.0.7'` once and drop the flag
everywhere — see [Remote host](#remote-host). Nothing else changes, and you
never log into the node.

Send your own requests to the Envoy front door:
```bash
curl -s http://localhost:8081/v1/completions -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","prompt":"Hello","max_tokens":32}'
```

## Configuration

Selected by `nok8s.enabled: true` in a scenario (mutually exclusive with
`modelservice`/`standalone`/`fma`/`kustomize`). See
[`config/scenarios/guides/nok8s.yaml`](../config/scenarios/guides/nok8s.yaml) for a complete
single-GPU example. Key fields (full defaults in
`config/templates/values/defaults.yaml`):

| Key | Meaning |
|-----|---------|
| `nok8s.runtime` | `docker` or `podman` (GPU flag switches to CDI for podman) |
| `nok8s.connection` | Host whose runtime runs the stack. `localhost` (default), or `ssh://[user@]host[:port][/socket]`. A bare `10.0.0.7` / `user@node` is read as `ssh://`. Settable per run with `--set nok8s.connection=…` instead of editing the scenario. See [Remote host](#remote-host) |
| `nok8s.sshIdentity` | SSH private key for a remote connection (default: the agent / `~/.ssh` keys) |
| `nok8s.sshArgs` | Extra `ssh`/`scp` options. **Replaces** the defaults (`BatchMode=yes`, `ConnectTimeout=10`), so restate them if you override |
| `nok8s.hfTokenEnv` | Host env var passed to vLLM for HF auth |
| `nok8s.workspaceHostDir` | Host dir where EPP/Envoy configs are staged + bind-mounted |
| `nok8s.vllm.{image,tag,hostPort,tensorParallel,accelerator,gpus,deviceArgs,shmSize,replicas,extraArgs}` | vLLM worker(s); worker *i* is published on `hostPort + i` (see Accelerators for `accelerator`/`deviceArgs`) |
| `nok8s.epp.{image,tag,grpcPort,grpcHealthPort,metricsPort}` | Endpoint Picker |
| `nok8s.envoy.{image,tag,listenPort,adminPort}` | Envoy front door (the run target); `adminPort` is the admin interface, bound on the host (`--network host`) |
| `nok8s.nameSuffix` | Appended to every container name. Filled automatically with `-<stack>` in a multi-stack scenario (see below); leave empty for one stack |
| `model.{name,huggingfaceId}` | Set both; standup uses `name`, run reads `huggingfaceId` |

Sizing (fp16, single GPU): ~16 GB → 7–8B · ~24 GB → 8B · ~40 GB → 14B ·
~80 GB → 32B. For bigger models use `nok8s.vllm.extraArgs`
(e.g. `["--max-model-len","16384","--gpu-memory-utilization","0.95"]`) or an
FP8 checkpoint.

## Accelerators

The router (EPP + Envoy) and the benchmark harness are accelerator-agnostic;
only the **vLLM worker** is accelerator-specific. Select the accelerator with
`nok8s.vllm.accelerator` and set `nok8s.vllm.image` to the matching vLLM
backend. Only **NVIDIA is validated end-to-end**; the others use each backend's
documented device flags.

| `accelerator` | Device flags emitted | vLLM image (example) |
|---------------|----------------------|----------------------|
| `nvidia` (default) | `--gpus all` (docker) / `--device nvidia.com/gpu=all` (podman) | `vllm/vllm-openai` |
| `amd` | `--device /dev/kfd --device /dev/dri --group-add video` | `rocm/vllm` |
| `intel` | `--device /dev/dri` | Intel vLLM XPU image |
| `gaudi` | `--runtime=habana -e HABANA_VISIBLE_DEVICES=all` | Habana vLLM image |
| `cpu` | *(none)* | vLLM CPU build |
| `spyre` | *(none -- set `deviceArgs`)* | IBM vLLM-Spyre image |

For anything not covered by a preset — including **IBM Spyre / AIU** — use the
raw escape hatch `nok8s.vllm.deviceArgs`, which overrides the preset entirely:

```yaml
nok8s:
  vllm:
    accelerator: spyre
    image: <ibm-vllm-spyre-image>
    deviceArgs: ["--device", "/dev/vfio/vfio", "--device", "/dev/vfio/<grp>"]
    extraArgs: ["--..."]   # any Spyre-specific vLLM flags
```

Step 00 probes the accelerator when it can (`nvidia-smi`/`rocm-smi`/`xpu-smi`/
`hl-smi`); `cpu`/`spyre`/custom are not probed (a note is logged) — ensure the
device and image match yourself.

## Remote host

By default the containers run on the machine that runs `llmdbenchmark`. Set
`nok8s.connection` and they run somewhere else instead:

```yaml
nok8s:
  enabled: true
  connection: 10.0.0.7          # or ssh://bench@10.0.0.7, or a hostname
```

That is the whole change. Everything else — `standup`, `smoketest`, `run`,
`teardown` — works exactly as it does locally:

```bash
llmdbenchmark --spec my-scenario.yaml standup --methods nok8s
llmdbenchmark --spec my-scenario.yaml smoketest
llmdbenchmark --spec my-scenario.yaml run
llmdbenchmark --spec my-scenario.yaml teardown --methods nok8s
```

### Without editing the scenario (`--set`)

The node's address is usually a property of *this run*, not of the scenario, so
it does not have to be committed to a file. `--set` takes any dotted path into
the merged stack config, and every subcommand accepts it:

```bash
llmdbenchmark --spec my-scenario.yaml standup --methods nok8s --set nok8s.connection=10.0.0.7
```

`run`, `smoketest` and `teardown` take the same flag, and each phase needs it
for a different reason:

| Phase | What `nok8s.connection` decides |
|-------|---------------------------------|
| `standup` | Which host's runtime starts vLLM / EPP / Envoy, and where the configs are staged |
| `smoketest` | Which address is probed — the node's (`http://10.0.0.7:8081`), from the client |
| `run` | Which host's runtime runs the harness container, and where its inputs are staged and results pulled from |
| `teardown` | Which host's containers are removed |

Several keys fit in one flag (comma-separated), and the flag repeats:

```bash
--set 'nok8s.connection=ssh://bench@10.0.0.7:2222,nok8s.sshIdentity=/keys/id_ed25519'
```

The override is echoed at render time, which is worth reading back:

```
[nok8s-single] Scenario override: nok8s.connection: 'localhost' -> '10.0.0.7'
```

**`--set` applies to one invocation, so it has to be repeated on every phase.**
A `run` without it starts the harness on *your own* machine against
`localhost:8081`, where nothing is listening, while the stack sits idle on the
node — no error, just the wrong machine. The tell is the launch command: a
remote run reads `docker -H ssh://10.0.0.7/... run -d`, a local one is a bare
`docker run -d`. Likewise a `teardown` without it leaves the node's containers
running. To set it once for a whole session, use the env var instead:

```bash
export LLMDBENCH_SET='nok8s.connection=10.0.0.7'
```

Precedence is `scenario` < `--cluster-config` < `--set`, so this overrides a
`connection` already in the scenario file.

> **A typo in the last path segment still renders, and deploys wherever the
> scenario said.** `--set nok8s.conection=10.0.0.7` (one `n`) gives
> `Errors: 0` — `nok8s` exists, so the unknown-path check passes and the
> misspelled key is merged in as a new, unused one while the real
> `connection` keeps its value. Render warns when the misspelling is close
> enough to a real sibling to name it:
>
> ```
> ⚠️ [nok8s-single] override path 'nok8s.conection' does not exist, but
>    'nok8s.connection' does -- did you mean that? As written,
>    'nok8s.conection' is created as a new unused key and
>    'nok8s.connection' keeps its current value.
> ```
>
> It is a warning, not an error, because free-form blocks (e.g.
> `kustomize.guideVariableOverrides`) gain new keys by design — so a key too
> far from any sibling to be a plausible misspelling passes without comment.
> The other tell is the echoed override line: a working one reads
> `'localhost' -> '10.0.0.7'`, a typo'd one reads `<unset> -> '10.0.0.7'`. A
> typo in a *parent* segment (`nok8ss.connection`) warns too. All of this is
> how `--set` behaves tool-wide, not just here.

Guardrails still apply through `--set`: `--set nok8s.connection=tcp://10.0.0.7:2375`
fails at render with the reason, before anything is launched.

### Accepted values

| `nok8s.connection` | Meaning |
|--------------------|---------|
| `localhost` (default), `local`, `127.0.0.1`, a `unix://` or `/…` socket path | The local runtime — commands are byte-identical to a pre-`connection` release |
| `10.0.0.7`, `node1` | Read as `ssh://` with **no username**, so SSH picks one — see [Usernames](#usernames-when-yours-differs-from-the-nodes) |
| `bench@node1`, `remote@10.0.0.7` | Read as `ssh://` with an explicit username |
| `ssh://[user@]host[:port][/socket-path]` | Explicit. `port` is the **SSH** port; `socket-path` is the daemon socket on the node (rootless podman: `/run/user/<uid>/podman/podman.sock`) |
| `tcp://…` | **Refused**, with an error at render time |

### Why SSH, and why not `tcp://`

Both runtimes have a native SSH transport, so nothing has to be reconfigured
on the node and no tunnel has to be opened by hand:

```bash
docker -H ssh://bench@10.0.0.7 run ...
podman --url ssh://bench@10.0.0.7/run/user/1000/podman/podman.sock run ...
```

`tcp://` is refused on purpose. A docker/podman socket bound to a TCP port with
no TLS and no authentication grants root on that node to anyone who can reach
the port — mounting `/` into a privileged container is a one-liner. Since the
SSH transport needs no daemon changes at all, there is no case where opening
that port is the better trade.

### Usernames, when yours differs from the node's

`nok8s.connection` carries the username, and a **bare** address carries *none* —
it is not silently your local one:

```bash
--set nok8s.connection=10.0.0.7           # no username: ssh decides
--set nok8s.connection=remote@10.0.0.7    # explicit
--set nok8s.connection=ssh://remote@10.0.0.7:2222   # explicit, non-default SSH port
```

With no username, `ssh` applies its own resolution order — a matching `Host`
block in `~/.ssh/config` first, then `$USER`. So if you are `local` here and the
node only knows `remote`, a bare `10.0.0.7` tries `local@10.0.0.7` and fails
authentication. Either name the user in the connection, or let `~/.ssh/config`
supply it:

```
Host 10.0.0.7
    User remote
    IdentityFile ~/.ssh/id_ed25519
```

One value is enough — the username reaches all three places that need it: the
runtime client (`docker -H ssh://remote@10.0.0.7/var/run/docker.sock`), the
`ssh` probes, and `scp` staging.

Note that `~` in `nok8s.workspaceHostDir` and `nok8s.vllm.hfCacheDir` expands
against the **node's** `$HOME` (read live with `ssh … printenv HOME`), so it
becomes `/home/remote/…`, never your local `/Users/local/…`. A `~other/path`
names a specific other user and is left alone.

### Authentication: keys, not passwords

Password authentication is not supported. The connection is opened with
`-o BatchMode=yes`, which disables every prompt, because a standup issues dozens
of commands — a password would be asked for repeatedly, and a prompt nobody can
see is indistinguishable from a hang.

Preflight (step 00) turns that into an actionable failure rather than a timeout:

```
Cannot reach the 'docker' daemon at ssh://remote@10.0.0.7/var/run/docker.sock: …
Check that 'ssh remote@10.0.0.7 true' succeeds without a prompt (key-based auth,
key in the agent or nok8s.sshIdentity, host key already known) and that 'docker'
is running there.
```

One-time setup:

```bash
ssh-keygen -t ed25519                # if you have no key
ssh-copy-id remote@10.0.0.7          # asks for the password, this once
ssh remote@10.0.0.7 true             # the gate: must succeed silently
```

That last command is exactly what the tool needs to work. If it prompts,
standup will fail.

- **Passphrase-protected key** — load it into the agent once
  (`ssh-add ~/.ssh/id_ed25519`). The agent satisfies `BatchMode=yes`, since no
  terminal prompt is involved.
- **Non-default key** — `--set nok8s.sshIdentity=/Users/local/.ssh/id_node`.
- **First connection to an unknown host** — the host key must already be in
  `known_hosts` (`ssh-keyscan -H 10.0.0.7 >> ~/.ssh/known_hosts`), or
  `BatchMode` fails on the confirmation prompt.

> **`sshIdentity` does not reach the `docker` client.** It is applied to every
> `ssh`/`scp` probe (`-i /path`) and to podman (`podman --url … --identity
> /path`), but `docker -H ssh://…` takes no `-i` — docker shells out to your
> system `ssh`, so it reads the key from the agent or `~/.ssh/config`. With
> docker **and** a non-default key, put it in `~/.ssh/config` as `IdentityFile`;
> `sshIdentity` alone gives you a passing preflight and failing container
> commands.

`nok8s.sshArgs` replaces the default args, so `BatchMode` *can* be switched off
(`sshArgs: ["-o", "BatchMode=no", "-o", "ConnectTimeout=10"]`). This is not a
supported way to use a password -- it affects only the `ssh`/`scp` probes, not
the `docker -H` / `podman --url` calls, so you get repeated prompts and a
standup that still stalls. Use keys.

### What the node needs

Only what a bare-metal box already has:

- **A container runtime** (`docker` or `podman`) running, and your SSH user able
  to use it (in the `docker` group, or rootless podman with its socket active —
  `systemctl --user enable --now podman.socket`).
- **Key-based SSH that works non-interactively** — passwords are not supported.
  Verify with the exact command the preflight suggests:
  ```bash
  ssh remote@10.0.0.7 true     # must succeed with no prompt
  ```
  See [Authentication](#authentication-keys-not-passwords) for the setup, and
  [Usernames](#usernames-when-yours-differs-from-the-nodes) when your local user
  is not the node's.
- **`curl`, `timeout`, and `ss` or `lsof`** — checked by step 00 on the node.

No `llmdbenchmark`, no Python, and no cluster tooling is installed there, and
you never need an interactive login.

### What runs where

The distinction that matters is *client* versus *daemon host*, because a path or
a `localhost` means different things on each side:

| | Runs on | Why |
|---|---------|-----|
| `llmdbenchmark` itself | Client | It only drives the runtime client |
| vLLM / EPP / Envoy | Node | That is the point |
| **Benchmark harness** | **Node** | Driving load from the client would add the SSH round-trip to every request and report it as the stack's latency |
| EPP/Envoy configs | Pushed to the node (`scp`) | Bind-mount sources are resolved by the daemon |
| Readiness probes (`curl`) | Node | `curl localhost:8081` from the client would probe the client |
| Container logs | Pulled to the client | A failed standup is diagnosable without SSHing in |
| Results | Pulled to the client | You get the same `workspace/results/` tree as a local run |

Because the harness runs on the node, the rendered
`34_nok8s-containers.yaml` carries **two** endpoints, and they are identical
for a local stack:

| Field | Value | Used by |
|-------|-------|---------|
| `endpoint` | `http://localhost:<listenPort>` | The harness container (`--network host`, on the node) |
| `clientEndpoint` | `http://<node>:<listenPort>` | The smoketest and your own `curl`, from the client |

A `run` that follows a `standup` in the same command inherits `endpoint`
directly. A standalone `llmdbenchmark run` has nothing in memory, so it starts
from the client URL and step 07 swaps in `endpoint` from the rendered spec
before launching the harness. An explicit `--endpoint-url` is never swapped --
if you name a target, that is the target.

So `curl` the node, not localhost:
```bash
curl -s http://10.0.0.7:8081/v1/completions -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","prompt":"Hello","max_tokens":32}'
```

Envoy's `listenPort` has to be reachable from the client for the smoketest (or
tunnel it: `ssh -L 8081:localhost:8081 bench@10.0.0.7`). The vLLM and EPP ports
never need to be — they are probed on the node.

Where things land on the node:

| Path | Contents |
|------|----------|
| `nok8s.workspaceHostDir` (default `~/.llmdbench/nok8s`) | Staged EPP/Envoy configs. `~` is expanded against the **node's** `$HOME`, not yours |
| `nok8s.vllm.hfCacheDir` (default `~/.cache/huggingface`) | Model weights, so a re-run does not re-download |
| `~/.llmdbench/nok8s-runs/<stack>/<workspace-name>/` | Per-run harness inputs and results. Kept after the pull, so a failed run stays inspectable |

The Hugging Face token is passed as a valueless `-e HUGGING_FACE_HUB_TOKEN`,
which both CLIs expand from your **client** environment. It reaches the vLLM
container without ever being written to the node's disk.

### Remote troubleshooting

- **`Cannot reach the 'docker' daemon at ssh://…`** — step 00 already ran
  `docker -H … info`, so the connection, not the config, is the problem. Work
  through `ssh <dest> true`, then whether your user can use the runtime there
  (`ssh <dest> docker info`).
- **A prompt appears and the standup fails immediately** — `BatchMode=yes` is
  doing its job. Add the host key (`ssh-keyscan -H <host> >> ~/.ssh/known_hosts`)
  or load the key into your agent. Passwords are not supported at all — see
  [Authentication](#authentication-keys-not-passwords).
- **`Permission denied (publickey)` naming the wrong user** — the connection
  carried no username, so `ssh` used yours. Give it one
  (`--set nok8s.connection=remote@10.0.0.7`) or set `User` in `~/.ssh/config`;
  see [Usernames](#usernames-when-yours-differs-from-the-nodes).
- **`Failed to stage nok8s configs to …`** — fatal on purpose: docker silently
  turns a missing bind-mount source into an *empty directory*, so the EPP would
  come up with no endpoints file and route nothing. Check that
  `nok8s.workspaceHostDir` is writable by the SSH user.
- **Smoketest cannot reach the endpoint but standup passed** — standup probes
  from the node, the smoketest from the client. The stack is up; Envoy's
  `listenPort` is firewalled.
- **Paths containing `~` did not resolve** — step 00 warns when it cannot read
  `$HOME` on the node. Use absolute paths for `workspaceHostDir` and
  `hfCacheDir`.
- **It deployed locally and the node is untouched** — the connection never took
  effect. Check the render log for
  `Scenario override: nok8s.connection: 'localhost' -> '<IP>'`; `<unset> ->`
  means the `--set` path was misspelled, and no line at all means `--set` was
  missing from *this* command (it is per invocation — see
  [Without editing the scenario](#without-editing-the-scenario---set)). A
  targeted command logs `nok8s target: docker @ ssh://…` and every container
  command carries `-H ssh://…`.
- **The stack is on the node but the benchmark numbers look like localhost** —
  `run` was invoked without the connection, so the harness ran on the client
  against its own port 8081. Re-run `run` with the same `--set` you gave
  `standup`.

### Several nodes

`nok8s.connection` is per stack, so a multi-stack scenario can spread stacks
across nodes — and stacks on *different* nodes no longer contend for ports or
devices. The clash checks are deliberately conservative and treat all stacks as
sharing one host, so two stacks on different nodes still have to be given
distinct ports and container names. Benchmark them one at a time with
`--stack` (see [Several stacks on one host](#several-stacks-on-one-host)).

## Several stacks on one host

A scenario with more than one stack runs every stack's containers on the same
host, with no namespace to keep them apart, so each stack needs its own
identity. Two things are automatic and two are on you:

- **Container names** get a `-<stack name>` suffix, e.g. `vllm-0-chat`,
  `epp-chat`, `envoy-chat`. Without this, stack B's idempotency sweep
  (`docker rm -f epp`) deletes stack A's running router. Two stacks that
  still end up with the same container name (names differing only by
  punctuation, or a shared explicit `nok8s.nameSuffix`) are a render error.
- **`nok8s.workspaceHostDir`** gains a per-stack sub-directory, so the staged
  EPP/Envoy configs never overwrite each other.
- **Host ports are yours to assign.** They are never derived, because guessing
  would silently bind ports you did not ask for. Two nok8s stacks claiming the
  same port is a render error that names the port and the owning stack, and
  standup stops before any container starts.
- **Accelerators are yours to divide.** A worker with `replicas: 1` gets
  `--gpus all`, so two such stacks both claim every device and the second one
  runs out of memory. Give each stack its own devices (preflight warns when
  more than one stack is left unpinned). Total demand is the sum of
  `replicas x tensorParallel` over all stacks.

A single-stack scenario is unaffected: names stay `vllm-0` / `epp` / `envoy`
and the workspace stays `~/.llmdbench/nok8s`. Converting an existing one? Run
`teardown` first (or `docker rm -f vllm-0 epp envoy`): the unsuffixed
containers from the single-stack run are no longer matched by the suffixed
names, so teardown will not remove them and they keep holding their ports and
device memory.

Give every stack after the first a distinct set of six ports (more, with
`replicas > 1`: worker *i* takes `hostPort + i`) and its own devices:

```yaml
scenario:
  - name: chat
    nok8s:
      enabled: true
      vllm:
        gpus: "device=0"        # podman: nok8s.vllm.deviceArgs
      # ... first stack keeps the default ports: 8000, 8081, 19000, 9002/9003/9090

  - name: code
    nok8s:
      enabled: true
      vllm:
        hostPort: 8100
        gpus: "device=1"
      epp:
        grpcPort: 9102
        grpcHealthPort: 9103
        metricsPort: 9190
      envoy:
        listenPort: 8181
        adminPort: 19100
```

Each stack gets its own Envoy front door, so there is no scenario-wide
endpoint: benchmark them one at a time with `--stack`, which resolves that
stack's `listenPort`.

```bash
llmdbenchmark --spec my-scenario.yaml run --stack chat   # -> http://localhost:8081
llmdbenchmark --spec my-scenario.yaml run --stack code   # -> http://localhost:8181
```

`run` without `--stack` (or an explicit `--endpoint-url`) fails on a
multi-stack nok8s scenario rather than benchmarking the first stack's Envoy
and filing the results under every stack's name.

## How it maps to the pipeline

| Phase | nok8s behaviour |
|-------|-----------------|
| standup step 00 | Preflight: runtime / GPU / ports / token (replaces helm/kubectl checks). For a remote `connection`, `<runtime> info` doubles as the connection test, and the GPU/port/tool probes run on the node |
| standup steps 02–05 | Skipped (no namespace, PVC, model-download Job) |
| standup step 06 | `step_06_nok8s_deploy` launches the containers, waits for `/v1/models`, records `http://localhost:<listenPort>`. Remote: pushes the staged configs first, expands `~` against the node's `$HOME`, probes readiness on the node |
| smoketest steps 00–01 | Cluster-free probes: `GET /v1/models` and `POST /v1/completions` against the Envoy front door, read from the rendered `34_nok8s-containers.yaml` (no pods, Service or route). Dials `clientEndpoint`, since it runs on the client |
| run step 03 | Endpoint resolves to the Envoy URL without a cluster query: `localhost` for a local stack, the node for a remote one |
| run step 07 | `step_07_deploy_harness_local` runs the harness image with `--network host`; results land in `workspace/results/` via a bind-mount. Remote: the container runs **on the node** (so the measured latency is the stack's), using the in-host `endpoint`, with inputs pushed and results pulled back |
| teardown step 06 | `step_06_nok8s_teardown` removes the containers on the host the spec names; an unresolvable `connection` fails the step rather than falling back to the local runtime |

## Multiple GPUs

Two modes, driven by `tensorParallel` and `replicas`:

**One model sharded across GPUs (tensor parallelism)** — serve a large model:
```yaml
nok8s:
  vllm:
    tensorParallel: 4      # shard one model across 4 GPUs
    replicas: 1
    shmSize: "40g"         # bump for NCCL/RCCL with TP > 1
```
→ `--gpus all --tensor-parallel-size=4`.

**Multiple independent workers (throughput / router load-balancing)** — set
`replicas: N`. Each worker is published on `hostPort + i`, added to the EPP
endpoints file (so the router load-balances / prefix-routes across them), and
**pinned to its own slice of `tensorParallel` GPU indices** (replica *i* →
devices `i*TP .. i*TP+TP-1`) via the accelerator's visible-devices env
(`CUDA_VISIBLE_DEVICES` / `HIP_VISIBLE_DEVICES` / `ZE_AFFINITY_MASK`) so workers
don't contend for the same GPUs.

**Total GPUs used = `replicas × tensorParallel`.** Examples on an 8-GPU host:
| Goal | `replicas` | `tensorParallel` |
|------|-----------|------------------|
| One big model sharded 8-way | 1 | 8 |
| 8 workers, 1 GPU each (max throughput) | 8 | 1 |
| 2 workers, each sharded over 4 | 2 | 4 |

Step 00 warns if `replicas × tensorParallel` exceeds the detected GPU count
(NVIDIA). Per-replica pinning uses index-based env vars for nvidia/amd/intel;
for gaudi/spyre or custom wiring, set `nok8s.vllm.deviceArgs` (which disables
auto-pinning and puts you in control).

## Troubleshooting

- **Preflight fails on runtime** — install docker/podman or set `nok8s.runtime`.
- **vLLM container exits at load** — usually a too-large model for VRAM or a bad
  HF token; check `docker logs vllm-0` (`docker logs vllm-0-<stack>` in a
  multi-stack scenario). Lower the model size or add
  `--max-model-len`/`--gpu-memory-utilization` via `nok8s.vllm.extraArgs`.
- **Envoy 503** — a worker isn't up; confirm `curl http://localhost:8000/v1/models`.
- **podman + GPU** — ensure the CDI spec exists: `nvidia-ctk cdi list` should show
  `nvidia.com/gpu=all`.
- **Ports busy** — a stale run; `teardown --methods nok8s`, or change the ports.
- **Remote (`nok8s.connection`)** — see
  [Remote troubleshooting](#remote-troubleshooting).
