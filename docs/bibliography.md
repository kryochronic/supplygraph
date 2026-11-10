# SupplyGraph — Bibliography

*Started 31 Oct 2026 · Abhinav Tripathi*

These are the sources behind the decisions in the project register and the papers built on SupplyGraph. Cite them as `[B1]`, `[B2]` and so on; numbers are stable, and new sources are appended.

Each entry says how it was used:

- **read**: the page was fetched and read during the work, on the access date given.
- **ran**: the software was installed and exercised.
- **inspected**: the source code was read.
- **not re-read**: cited from prior knowledge; check it before quoting in a paper.

§D lists figures quoted in the docs and conversations that have no source yet. None of them should reach a paper until they have one.

---

## A. Standards and specifications

**[B1]** Model Context Protocol. *Authorization: Client Registration.* Specification version 2026-07-28.
<https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration>, accessed 6 Oct 2026. **Read.**
Used for:
- the registration order: pre-registration, then Client ID Metadata Documents, then Dynamic Client Registration;
- DCR marked deprecated;
- the CIMD requirements on clients and authorization servers.
→ ADR-001, F6, D-26 context.

**[B2]** IETF. *RFC 9728: OAuth 2.0 Protected Resource Metadata.* 2025. <https://www.rfc-editor.org/rfc/rfc9728>. **Not re-read.**
Used for: the `/.well-known/oauth-protected-resource` document and the `resource_metadata` pointer in `WWW-Authenticate`. → F1, MCP guide Stage 2.

**[B3]** IETF. *RFC 8707: Resource Indicators for OAuth 2.0.* 2020. <https://www.rfc-editor.org/rfc/rfc8707>. **Not re-read.**
Used for: the `resource` parameter and audience-bound tokens. → F6.

**[B4]** IETF. *RFC 7591: OAuth 2.0 Dynamic Client Registration Protocol.* 2015. <https://www.rfc-editor.org/rfc/rfc7591>. **Not re-read.**
→ F2 (realm DCR policies).

**[B5]** IETF. *RFC 7636: Proof Key for Code Exchange by OAuth Public Clients (PKCE).* 2015. <https://www.rfc-editor.org/rfc/rfc7636>. **Not re-read.**
→ `deploy/scripts/oauth-walkthrough.py`.

**[B6]** IETF. *RFC 8628: OAuth 2.0 Device Authorization Grant.* 2019. <https://www.rfc-editor.org/rfc/rfc8628>. **Not re-read.**
→ `deploy/scripts/sg-token.sh`.

**[B7]** IETF. *RFC 9068: JSON Web Token (JWT) Profile for OAuth 2.0 Access Tokens.* 2021. <https://www.rfc-editor.org/rfc/rfc9068>. **Not re-read.**
→ `mcp-server/.../auth.py`.

**[B8]** IETF. *RFC 8414: OAuth 2.0 Authorization Server Metadata.* 2018. <https://www.rfc-editor.org/rfc/rfc8414>. **Not re-read.**
Used for: the discovery order in the walkthrough script.

**[B9]** IETF OAuth Working Group. *OAuth Client ID Metadata Document*, draft-ietf-oauth-client-id-metadata-document-00. <https://www.ietf.org/archive/id/draft-ietf-oauth-client-id-metadata-document-00.html>. **Not re-read.** Linked from [B1].
→ F6.

**[B10]** IETF OAuth Working Group. *The OAuth 2.1 Authorization Framework*, draft-ietf-oauth-v2-1. <https://datatracker.ietf.org/doc/draft-ietf-oauth-v2-1/>. **Not re-read.**
Used for: HTTPS required except on loopback, which is why local TLS runs through mkcert.

## B. Documentation and articles

**[B11]** WorkOS. *The biggest MCP spec update ships July 28: What changes for AI agent authentication.* <https://workos.com/blog/mcp-2026-spec-agent-authentication>, accessed 6 Oct 2026. **Read.**
Used for:
- the `Mcp-Session-Id` header and the initialize handshake removed;
- capabilities carried in `_meta` on every request;
- round-robin load balancing without sticky sessions.
→ F7 and the stateless transport (ADR-001).

**[B12]** Ollama. *GPU* (documentation, readthedocs mirror). <https://ollama.readthedocs.io/en/gpu/>, accessed 31 Oct 2026. **Read.**
Used for:
- ROCm on Windows supports only the RX 7000 series and RX 6800/6900 and up, so the RX 5500M is absent;
- `HSA_OVERRIDE_GFX_VERSION` exists, for Linux only.
→ K nodes table, D-28.

**[B13]** ChharithOeun. *GPU compatibility* (community table), ollama-amd-windows-setup. <https://github.com/ChharithOeun/ollama-amd-windows-setup/blob/master/gpu_compatibility.md>, accessed 31 Oct 2026. **Read.**
Used for: gfx1012 (RDNA 1, RX 5500 family) is not in official ROCm; Vulkan or a community fork is the route. The table lists the RX 5500 XT, not the 5500M. → K nodes table.

**[B14]** M. Larabel. *ollama 0.12.11 Brings Vulkan Acceleration.* Phoronix. <https://www.phoronix.com/news/ollama-0.12.11-Vulkan>, accessed 31 Oct 2026. **Read.**
Used for: Vulkan as an alternative to ROCm and CUDA, enabled with `OLLAMA_VULKAN=1`. → K3.

**[B15]** R. Glukhov. *ROCm vs Vulkan for AMD Local LLM Hosting: 2026 Guide.* <https://www.glukhov.org/llm-hosting/comparisons/amd-rocm-vs-vulkan-llm-hosting/>, accessed 31 Oct 2026. **Read.**
Used for:
- Ollama's hardware page says Vulkan is on by default when the backend is installed;
- Vulkan covers more Radeon hardware than ROCm;
- a silent fallback to the CPU is a known failure, so check the server log.
→ K3.

**[B16]** Hussain (hussainweb). *Ollama quietly stopped using my AMD iGPU.* 16 Jul 2026. <https://hussainweb.me/blog/ollama-amd-igpu-vulkan/>, accessed 31 Oct 2026. **Read.**
Used for: `OLLAMA_IGPU_ENABLE=1`, and `library=Vulkan` in the log as the check. Only relevant for built-in GPUs.

**[B17]** Ollama. *Hardware support.* <https://docs.ollama.com/gpu>. **Not fetched** (blocked from the workspace). This is the canonical page that [B12] mirrors and [B15] quotes; read it directly before citing Vulkan defaults.

**[B18]** Keycloak. *Integrating with Model Context Protocol (MCP).* <https://www.keycloak.org/securing-apps/mcp-authz-server>, accessed 6 Oct 2026. **Read.**
Used for:
- the experimental features `cimd` and `resource-indicators` in 26.8;
- the anonymous DCR policies (Trusted Hosts, Allowed Client Scopes);
- the CIMD executor options.
→ F2, F6.

**[B19]** endoflife.date. *Keycloak.* <https://endoflife.date/keycloak>, accessed 6 Oct 2026. **Read.**
Used for: 26.8.0 released 1 Oct 2026, with only the latest minor version supported. → image tag in compose and k8s.

**[B20]** U.S. Securities and Exchange Commission. *Accessing EDGAR Data* (fair access policy). <https://www.sec.gov/os/accessing-edgar-data>. **Not re-read.**
Used for: a declared User-Agent, and the 10 requests per second limit per requester. → `sec_nport.py`, F7.

**[B21]** CNCF. *llm-d* (project page). <https://www.cncf.io/projects/llm-d/>. Seen in search results, **not read**.
Used for: llm-d is a CNCF project. → K, Phase 3.

**[B22]** llm-d. *Infrastructure & Environments.* <https://llm-d.ai/docs/infrastructure>, accessed 31 Oct 2026. **Read.**
Used for:
- tested accelerators: NVIDIA L4, A100, H100, H200, B200 and newer; AMD MI250X or newer; Google TPU v5e, v6e and newer;
- Kubernetes 1.29 or newer (1.33+ recommended);
- vLLM 0.10.0 or newer;
- minikube supported for single-host development;
- older hardware described as "best-effort".
→ K15, D-31.

**[B23]** aicybr. *llm-d 1.0: Kubernetes Inference Routing, Deployment and Upgrade Guide.* 25 Oct 2026. <https://aicybr.com/blog/llm-d-1-0-kubernetes-inference-routing-quickstart-upgrade>, accessed 31 Oct 2026. **Read.**
Used for:
- 1.0 released 20 Oct 2026;
- the endpoint picker weighs cached context, load and predicted latency;
- prefix-aware routing;
- separate prefill and decode pools;
- the default example uses four H100s, "not a single-consumer-GPU deployment";
- CPU and Intel XPU paths exist in the recipe matrix.
→ K, Phase 3. This is a secondary source, so confirm the release date on the llm-d GitHub releases page before citing it.

**[B24]** llm-d. *llm-d proposal.* <https://github.com/llm-d/llm-d/blob/main/proposals/llm-d.md>. Seen in search results, **not read**. Primary source for the design; read before writing K18.

**[B25]** E. Gallen. *llm-d: Multi-Accelerator LLM Inference on Kubernetes.* KubeCon + CloudNativeCon North America 2025 (slides). <https://static.sched.com/hosted_files/kccncna2025/13/llm-d-Multi-Accelerator-LLM-inference-on-Kubernetes-Erwan-Gallen-KubeCon-NA-2025.pdf>. Seen in search results, **not read**.

## C. Software

| Software | Version | How used | Claims made about it |
|---|---|---|---|
| MCP Python SDK (`mcp`) | 2.3.0 | **Inspected and ran:** `MCPServer`, `AuthSettings`, `TokenVerifier`, stateless Streamable HTTP | All verified in tests |
| Keycloak | 26.8.0 | **Ran:** realm bootstrap, DCR + PKCE flow, CIMD advertisement, features flag | Secure cookies over HTTP (observed); `KC_FEATURES=''` refused (observed) |
| Caddy | 2.10.2 | **Ran:** TLS termination, routing by host | Recasing of `WWW-Authenticate` (observed) |
| mkcert | 1.4.4 | **Ran** | — |
| kustomize / kubeconform | 5.7.1 / 0.7.0 | **Ran:** builds and schema validation | — |
| Compose specification schema | main, Oct 2026 | **Ran:** `docker-compose-mcp.yml` validated | — |
| sqlmodel | 0.0.25, 0.0.48 | **Ran** | 0.0.48 rejects naive datetimes (observed) |
| llama.cpp (RPC mode) | — | **Not re-read** | Mixes Metal, CUDA and Vulkan machines; runs natively on Windows. **Check before K11.** |
| exo | — | **Not re-read** | Built mainly around Macs. **Check before citing.** |
| LiteLLM | — | **Not re-read** | Least-busy routing; per-deployment parallel limit. **Check before K5.** |
| Tailscale | — | **Not re-read** | Stable machine names across Windows and macOS |
| Docker Desktop (macOS, WSL2) | — | **Not re-read** | No Apple GPU inside containers; NVIDIA GPU available in WSL2; no path for the RX 5500M. **Check before citing.** |
| k3d / k3s | — | Config written, **never run** | Traefik and metrics-server built in |
| vLLM | — | **Not re-read** | Needs the whole model in GPU memory; no Metal or Vulkan backend. **Check before K15.** |
| llm-d | 1.0 | Read about it ([B22][B23]), **not run** | See [B22]/[B23] |

## D. Figures quoted without a source yet

All are from memory or rough calculation. Each needs a citation, or a measurement from K1/K12, before it goes into a paper.

| Figure | Where it was used | How to source it |
|---|---|---|
| 1 token ≈ 4 characters ≈ ¾ of an English word | context-window explanation, `llm.py` estimate | Tokenizer documentation, or count with Qwen's tokenizer on a real annual report |
| An annual-report page ≈ 600–900 tokens; a 300-page report ≈ 250–400k | context-window explanation | Measure: tokenize two reports from `reports/` |
| English Wikipedia plain text ≈ 20 GB | paper idea (29 Oct) | Wikimedia dump statistics |
| Frontier training sets ≈ 15–20T tokens; raw web crawl in petabytes | paper idea (29 Oct) | Model cards or technical reports; Common Crawl statistics |
| Qwen 72B ≈ 145 GB at 16-bit, ≈ 40 GB at 4-bit; Qwen3 14B ≈ 9 GB at 4-bit; Qwen3 30B-A3B ≈ 19 GB at 4-bit; a 70B model at 4-bit ≈ 40 GB | model sizing, K12 | Ollama library pages (file sizes) or `ollama show` |
| Qwen3 14B context memory ≈ 5 GB at 32k tokens | Mac sizing | Assumed 40 layers × 8 KV heads × 128 dimensions at 16-bit; check against the model card, or measure memory at 16k and 32k with `make qwen-check` |
| macOS lets the GPU use ≈ 70–75% of unified memory (≈ 27 GB of 36 GB) | Mac sizing | Apple Metal documentation (`recommendedMaxWorkingSetSize`) or measure |
| Memory bandwidth: M3 Pro ≈ 150 GB/s; GTX 1060 ≈ 192 GB/s; dual-channel DDR4 ≈ 50 GB/s | GPU comparisons | Apple and NVIDIA spec sheets; laptop memory spec |
| Ollama's default context window is a few thousand tokens | K1 truncation guard | [B17], or observe: run `disg llm-check` with and without `OLLAMA_CONTEXT_LENGTH` |
| "Speed falls as nodes are added" for split inference | D-29, K12 | That is the hypothesis K12 tests; report the measurement, not this sentence |
| The Dell E5570 has 2 cores (i5-6300U assumed) and DDR4, not DDR3 | D-30 | `lscpu` and `sudo dmidecode -t memory` on the Dell |
| How much of a `categorise` prompt is the shared start (instructions + taxonomy) | Q18, K16 | Measure from `disg categorise <KEY> --dry-run` |

## E. Measured in this project

These are SupplyGraph's own results. Cite the results file, not a website.

- J1 parse check of 1,253 broker PDFs: `experiments/rest-in-peace/results/j1_parse.json`.
- J5 token- and scope-check cost, in microseconds: `experiments/rest-in-peace/results/j5_lock_*.json`.
- K1 and K12 results will land in `bench/results/` or a `results/` folder under the fleet work, with the machine, model digest and settings recorded alongside.
