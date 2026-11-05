<div align="center">
  <h1>AuroraSpecDominion</h1>
</div>

<p align="center">
  <img src="assets/icon.png" alt="logo" width="300">
</p>

**AuroraSpecDominion** is a graphical Python application for converting unstructured requirement documents into structured, review-ready test cases. It ingests sources such as PDF specifications, applies deterministic parsing, and uses a locally hosted Large Language Model (via Ollama) to draft consistent test case candidates. The interface provides a guided workflow, configurable generation parameters, and export options for CSV and Excel, enabling clean integration with established test engineering processes.

The system accelerates early-phase test development by producing high-quality initial drafts that can be validated and refined by test architects. This reduces manual effort while maintaining traceability, structure, and engineering discipline.

**AuroraSpecDominion** requires a locally running Ollama instance configured with a supported model (for example mistral or llama3) to enable the generation pipeline.

**AuroraSpecDominion** reflects three ideas that define its identity:

* Aurora signals emergence and clarity, capturing the tool's role in revealing structure within complex requirements.
* Spec grounds the system in specification analysis and test derivation.
* Dominion conveys control over a domain, expressing the tool's purpose: governing and formalizing test knowledge with precision.

Together, the name represents a system engineered to illuminate unstructured input, shape it into consistent test logic, and provide a controlled environment for producing formal test specifications.

---

<p align="center">
  <img src="assets/image.png" alt="screenshot" width="900">
</p>

---

## System Description

This architecture defines a desktop application that converts requirements from PDF files into structured test cases using a local LLM. It separates the user interface, background task execution, core data processing, and model communication into clear layers.

---

### 1. GUI Layer

* Purpose: Provides an interactive interface for users to load PDFs, configure options, and manage output.
* Key components:

  * `gui.py` launches the app.
  * Users select a PDF file, configure parameters (via `config.yml`), and click Generate.
  * A `QTableView` displays the generated test cases.
  * Users can export results to CSV or XLSX.

This layer communicates with the processing backend through worker threads to keep the UI responsive.

---

### 2. Worker Thread

* Purpose: Runs long operations asynchronously to avoid freezing the GUI.
* Key components:

  * Creates a `GeneratorTask` object.
  * Executes it as a `QRunnable` in a thread pool.
  * Emits Qt signals (`progress`, `result`, `error`) to update the UI.

This design ensures smooth user interaction while processing large PDFs or calling the LLM.

---

### 3. Core Logic

* Purpose: Handles the main document and data transformation pipeline.
* Key steps:

  * Reads PDFs using PyMuPDF (`fitz`).
  * Splits content into manageable chunks (batching and combining).
  * Loads a prompt file from `prompts/`.
  * Sends the prepared context to the local AI agent for test case generation.
  * Saves results to CSV or XLSX for export.

This layer forms the core processing pipeline between the raw document and final structured outputs.

---

### 4. Agent and LLM Integration

* Purpose: Generates structured test cases using a locally hosted LLM API.
* Workflow:

  * Initializes the agent with prompt and configuration.
  * Sends data via HTTP POST requests to the local LLM.
  * Parses JSON responses into structured test cases.

This decouples AI inference from the application's UI and processing logic.

---

### 5. Data I/O

* External files:

  * PromptFile (.txt): Custom prompt templates for guiding the LLM.
  * ConfigFile (`config.yml`): User-defined generation settings.
  * PDFInput: Source document containing requirements.
  * CSVOutput / XLSXOutput: Final test cases ready for external use.

---

### Key Design Characteristics

* Asynchronous execution keeps the interface responsive while heavy tasks run in the background.
* Modular structure: clear separation between UI, task management, core processing, and AI inference.
* Configurable and prompt-driven: allows easy customization through `config.yml` and prompt files.
* Local LLM support: enables offline or self-hosted AI inference without dependency on external services.

---

```mermaid
flowchart TD
    %% USER FLOW
    subgraph GUI_Layer
        GUI_Start["User Launches GUI - gui.py"]
        GUI_Load["Select PDF File"]
        GUI_Config["Configure Parameters"]
        GUI_Run["Click 'Generate'"]
        GUI_Display["Display Table - QTableView"]
        GUI_Export["Export to CSV/XLSX"]
    end

    %% WORKER THREAD
    subgraph Worker_Thread
        Worker_Create["Create GeneratorTask"]
        Worker_Run["Run QRunnable in ThreadPool"]
        Worker_Signals["Emit Qt Signals: progress, result, error"]
    end

    %% CORE LOGIC
    subgraph Core_Logic
        Core_ReadPDF["Read PDF with fitz (PyMuPDF)"]
        Core_Chunk["Chunk Pages - batch & combine"]
        Core_LoadPrompt["Load Prompt File from prompts/"]
        Core_Generate["Generate Cases via AgentLocal"]
        Core_SaveCSV["Save to CSV"]
        Core_SaveXLSX["Save to XLSX"]
    end

    %% AGENT AND LLM
    subgraph AgentLocal
        Agent_Init["Initialize Agent with Prompt and Config"]
        Agent_Post["POST to Local LLM HTTP API"]
        Agent_Parse["Parse JSON Test Cases"]
    end

    %% DATA
    subgraph Data_IO
        PromptFile["Prompt File (.txt)"]
        ConfigFile["config.yml"]
        PDFInput["Requirements PDF"]
        CSVOutput["Output .csv"]
        XLSXOutput["Output .xlsx"]
    end

    %% FLOW CONNECTIONS
    GUI_Start --> GUI_Load --> GUI_Config --> GUI_Run
    GUI_Run --> Worker_Create --> Worker_Run --> Worker_Signals
    Worker_Run --> Core_ReadPDF --> Core_Chunk
    Core_Chunk --> Core_LoadPrompt --> Core_Generate
    Core_Generate --> Agent_Init --> Agent_Post --> Agent_Parse --> Core_Generate
    Core_Generate --> Worker_Signals
    Worker_Signals --> GUI_Display --> GUI_Export
    GUI_Export --> Core_SaveCSV --> CSVOutput
    GUI_Export --> Core_SaveXLSX --> XLSXOutput

    %% EXTERNAL FILES
    PromptFile --> Core_LoadPrompt
    ConfigFile --> GUI_Config
    PDFInput --> Core_ReadPDF
```

* GUI Layer: interaction flow via PySide6 components.
* Worker Layer: asynchronous thread management using `QRunnable` and `Signal`.
* Core Logic: PDF processing, chunking, and integration with the agent.
* Agent: the bridge to the locally running LLM via HTTP POST.
* Data I/O: all key files involved in input/output and configuration.

---

## Features

* LLM-powered: uses a local language model to extract and generate test cases from PDF documents.
* Context retrieval (optional): lightweight TF-IDF retrieval augments prompts with the most relevant requirement snippets.
* PDF support: splits and processes PDFs into chunks for better LLM input handling.
* User-friendly GUI: built using PySide6 for an accessible desktop interface.
* Highly configurable: batch size, prompt combinations, case generation counts, and more.
* Exports: outputs test cases to `.csv` or `.xlsx` format.
* Multithreaded processing: uses Qt's QRunnable/Signals to keep the GUI responsive during generation.
* GPU-aware inference: configure Ollama hardware options (number of GPUs, primary GPU, GPU layers) directly from the GUI for faster local inference when a compatible GPU is available.
* Script-friendly progress: optional `tqdm` progress bar mirrors GUI updates when running the core pipeline from a shell.
* Verbose observability: heartbeat progress updates and chunk-level log messages surface long-running work in both the GUI console and CLI scripts.
* Response caching: persist chunk-level responses to disk to avoid re-querying the LLM on repeated runs.
* Instant insights: automatic coverage summary highlights missing fields, duplicate names, and the most referenced requirements after each run.
* Inline help: integrated HTML-based documentation for parameter explanations.

---

## Installation

### Prerequisites

* Python 3.9+
* PySide6
* pandas
* openpyxl
* PyMuPDF (the single PDF text extractor)
* requests
* pydantic (strict validation of model answers)
* yaml
* scikit-learn (optional, only for the legacy retrieval augmentation)
* tqdm (for optional CLI progress bar)

Install dependencies:

```bash
pip install -r requirements.txt
```

Or install the package itself, which provides the `spec2test` command:

```bash
pip install -e .            # core pipeline and CLI
pip install -e ".[gui]"     # plus PySide6 and the CSV/Excel exporters
pip install -e ".[dev]"     # plus pytest
```

---

## Usage

1. Launch the interface:

   ```bash
   spec2test-gui          # after pip install -e ".[gui]"
   python gui.py          # or straight from a checkout
   ```
2. Choose one or more specifications (PDF, TXT or Markdown) and, optionally, a
   project guidance file.
3. Check the settings on the left: endpoint mode and host, model name and
   sampling, and the generation limits - drafts per requirement, repair
   attempts, requirements per request, job budget.
4. Press **Generate drafts**. **Cancel** stops new requests; a running request
   ends within its read timeout.
5. Read the result in the tabs: **Test drafts** (steps, expectations,
   measurements with their evidence, open questions), **Requirements** (what
   was captured, with source positions), **Findings** (run report, open
   questions, rejected and unprocessed requirements), **Export table** and
   **Log**.
6. Export the drafts as `.csv` or `.xlsx`.

`config.yml` is read when the window opens and is never written automatically.
Changes in the window apply to the current session; *File > Write settings to
config.yml* stores them back deliberately.

### Automating from scripts

The current pipeline is driven through `spec2test.application.generate.
generate_drafts` or the `spec2test` command. The legacy flat path below remains
for existing scripts; it has no requirement traceability and prescribes the old
output format through the prompts in `prompts/legacy/`.

When you call `core.generate_test_cases` programmatically, set
`show_progress=True` to render a live `tqdm` bar that mirrors the progress
callbacks and pass a `logger` (`print` works) to stream block-by-block
updates:

```python
from core import AgentLocal, generate_test_cases

agent = AgentLocal(model="llama3:8b")
cases = generate_test_cases(
    pdf_path="requirements.pdf",
    agent=agent,
    combine=3,
    show_progress=True,
    logger=print,
)
```

The bar automatically falls back to standard logging when `tqdm` is not
installed.

### Accelerating repeated runs with disk caching

Re-running the generator with the same PDF and prompt is near-instant once
you enable the disk cache. Tick "Enable disk cache for chunk responses"
from the GUI (Performance and Cache panel) or configure it via `config.yml`:

```yaml
cache:
  enabled: true
  directory: ~/.tsg-cache
  max_entries: 256
  max_age_hours: 24
```

Each chunk prompt (model plus prompt content plus system instructions) is hashed and
stored alongside the repaired JSON response. On the next run the worker skips
LLM calls for any chunk that already exists within the TTL, providing dramatic
speed-ups when iterating on exports or prompt tweaks. Cache hits and misses are
logged in both the CLI and GUI log pane so you can monitor effectiveness.

### Reading the instant insight panel

After every generation the right-hand Insights box summarizes test quality:

* Complete cases - how many test cases include all required JSON fields.
* Requirement coverage - count of unique requirement identifiers and the
  top references.
* Missing sections - required fields still empty so you can re-run or
  manually patch them.
* Duplicate names - highlights test cases that share titles.

These quick heuristics make it easier to spot coverage gaps before exporting to
Excel or pushing the results downstream.

### Enabling GPU acceleration

Ollama automatically offloads work to the GPU when it detects a compatible
installation. TSG now exposes the relevant hardware knobs so you can steer that
behaviour from the GUI or from `config.yml`:

1. Install a GPU-capable Ollama build and vendor drivers (CUDA for NVIDIA,
   ROCm or Metal for AMD or Apple). Follow the official setup guide for your OS.
2. Verify GPU access directly in Ollama (outside of TSG) by running a quick
   prompt and observing the server log (it will mention that layers are being
   loaded onto the GPU), for example:

   ```bash
   ollama run llama3:8b "Hello"
   ```
3. In TSG's Model panel enable "GPU acceleration when available" to keep
   inference on the GPU. You can optionally override:

   * GPUs to use (`num_gpu`) - set to `Auto` to let Ollama pick or any
     positive integer to cap usage.
   * Primary GPU (`main_gpu`) - choose which GPU index should host KV cache
     allocations when you have multiple devices.
   * GPU layers (`gpu_layers`) - restrict the number of transformer layers to
     keep on the GPU when memory is limited.
   * CPU threads (`num_thread`) - fine tune CPU worker threads when you want
     to force CPU-only inference.

Disabling the checkbox forces Ollama to stay on the CPU (`num_gpu = 0`). All
settings persist to `config.yml` so scripted runs use the same hardware profile.

---

## Cloud and local operation (Spec2Test pipeline)

Next to the existing GUI there is a traceable pipeline with requirement capture,
strict validation and source references. It offers two explicit modes; there is
no implicit switching between them and no CLI fallback.

| Mode             | Host                       | Authentication                            |
| ---------------- | -------------------------- | ----------------------------------------- |
| `ollama_cloud` | `https://ollama.com`     | `Authorization: Bearer $OLLAMA_API_KEY` |
| `ollama_local` | `http://localhost:11434` | none                                      |

The API key is read from the environment only. It is never written to
`config.yml`, never logged, removed from error messages, and sent only to the
configured host; redirects are not followed.

Provide the key either by exporting it or through a git-ignored `.env` file in
the working directory:

```bash
cp .env.example .env && $EDITOR .env    # OLLAMA_API_KEY=...
```

An already exported variable always wins over the file. Only the variable name
is ever logged, never the value.

```bash
export OLLAMA_API_KEY=...          # cloud mode only

# list the models of the configured host and check a name
spec2test models --mode ollama_cloud --model gpt-oss:120b

# full example run: import, generate, validate, show drafts, store the run
spec2test run --spec requirements/sw_req_spec_ras.md --out runs

# force a mode regardless of the configuration
spec2test run --spec my_spec.pdf --mode ollama_cloud --model gpt-oss:120b

# offline demonstration without any network or credentials
spec2test run --spec tests/fixtures/specs/spec_a.md --mode fake
```

Without installing the package, run it from the repository root with
`PYTHONPATH=src python -m spec2test.cli …`.

The mode, host and timeouts live in `config.yml` and are used by the GUI, the
Qt worker and the CLI alike. In the GUI they are in the **Endpoint** group; GPU
options are disabled in cloud mode, because they only apply locally.

The GUI runs this same pipeline: load one or more PDF, TXT or Markdown files,
press **Generate**, and the result appears in four tabs - **Drafts** (test
cases with their preconditions, steps, expectations, measurements and open
questions), **Requirements** (what was captured, with the source position of
every requirement), **Findings** (the run report, open questions, rejected and
unprocessed requirements) and **Flat table** (which feeds the CSV/Excel
buttons). **Cancel** stops new requests; a running request ends within its
timeout.

A credential or host misconfiguration stops the whole run after the first
failure instead of repeating it for every requirement; the remaining
requirements are listed as `NOT STARTED`.

Exit codes: `0` completed, `2` partial, `3` cancelled, `1` failed. A progress
value of 100 % only means the processing ended - the run status says whether it
worked. A run is `completed` only when every planned requirement was processed:
if the job budget ends early, the unprocessed requirements are listed as
`NOT STARTED` and the run is `partial`.

Each run is stored under a collision-free ID (`run-<UTC timestamp>-<random>`),
and an existing result file is never overwritten silently.

Note on model names: the cloud API expects the name reported by `/api/tags`
(for example `gpt-oss:120b`). The local CLI and app show aliases such as
`gpt-oss:120b-cloud`. The two are never equated automatically; a mismatch is
reported as a warning.

What the pipeline guarantees:

* no input page is lost by block building, for any block size and overlap;
* every draft references captured requirements and keeps its source spans;
* missing limits, DTCs, signals or measurement methods are not invented - they
  are stored as open questions or explicitly marked proposals;
* an unknown requirement ID, a missing mandatory step or an invalid type blocks
  acceptance as a draft; unresolved questions block approval;
* only a deliberate tester decision sets a case to `approved`; a model answer
  always starts as `draft`, and an edit after approval creates a new revision.

---

## Configuration Model

TSG persists the UI state to `config.yml`. The file now uses a structured layout to improve readability and enable advanced options:

```yaml
model:
  name: llama3:8b
  temperature: 0.4
  top_p: 0.9
  top_k: 64
  max_tokens: 8192
  use_gpu: true
  num_gpu: null        # "null" = auto, set 0 to force CPU or >0 to cap usage
  main_gpu: null       # choose a GPU index when you have multiple devices
  gpu_layers: null     # limit how many layers stay on the GPU (auto by default)
  num_thread: null     # override CPU worker threads when forcing CPU mode
generation:
  cases_per_prompt: 6
  combine_pages: 3
  batch_size: 1
  parallel_prompts: 1
rag:
  enabled: true
  top_k: 2
  chunk_overlap: 1
cache:
  enabled: true
  directory: ~/.tsg-cache
  max_entries: 256
  max_age_hours: 24
paths:
  pdf_path: /abs/path/to/requirements.pdf
  prompt_path: /abs/path/to/system_prompt.txt
```

Legacy flat files remain supported and will be migrated to the nested layout
automatically when the GUI closes. Use the Context Enhancements panel in the
GUI to toggle retrieval augmentation, control the number of related chunks
appended to each prompt, and adjust chunk overlap. The Performance and Cache
panel mirrors the `cache` section so you can switch caching on and off, tweak
TTL, and point the cache at faster storage without hand-editing YAML.

---

## Project Structure

```shell
spec2test/
├── pyproject.toml              # installable package, CLI and GUI entry points
├── config.yml                  # read at startup, never written automatically
├── .env.example                # template for OLLAMA_API_KEY
├── gui.py                      # launcher for running from a checkout
├── core.py                     # legacy wrappers for existing scripts
├── src/spec2test/
│   ├── domain/                 # data model and rules, no I/O at all
│   │   ├── models.py
│   │   └── validation.py
│   ├── application/            # use cases and ports
│   │   ├── ports.py            # model, document import, run store, export
│   │   ├── contracts.py        # strict versioned model contract
│   │   ├── chunking.py         # block building without page loss
│   │   ├── ingest.py           # requirement capture with source spans
│   │   ├── prompts.py          # contract rules plus optional guidance
│   │   ├── assembly.py         # contract objects to domain objects
│   │   ├── generate.py         # generation, bounded repair, run status
│   │   └── legacy.py           # deprecated flat-shape bridge
│   ├── adapters/
│   │   ├── ollama/             # HTTP transport and model capabilities
│   │   ├── documents/          # PDF, TXT and Markdown import
│   │   ├── persistence/        # atomic JSON run storage
│   │   ├── export/             # CSV and Excel writers
│   │   └── fake/               # scripted and rule-based fake models
│   ├── ui/                     # PySide6 window and Qt workers
│   │   ├── app.py              # entry point (spec2test-gui)
│   │   ├── main_window.py
│   │   ├── workers.py          # DraftTask, ModelListTask
│   │   ├── models.py           # Qt table model
│   │   └── help_text.py        # in-app parameter reference
│   ├── config.py               # configuration dataclasses
│   ├── bootstrap.py            # configuration to adapters, .env loading
│   └── cli.py                  # example run and model listing
├── prompts/                    # project guidance, no format instructions
│   └── legacy/                 # the old format-prescribing prompts
├── requirements/               # synthetic rear-axle-steering requirements
├── tests/                      # unit, layering, UI and opt-in live tests
└── docs/L1-report.md           # delivery report
```

---

## Prompt Customization

* System prompts are stored in the `prompts/` directory.
* You can edit these text files to tailor how the LLM interprets and generates test cases.

---

## Example Use Case

1. A test architect uploads a 20-page requirements PDF.
2. The app chunks the document into batches.
3. Each chunk is fed into the LLM using the defined prompt.
4. The app assembles structured test cases and displays them.
5. The user exports the list to Excel for further QA workflows.

---

## Testing

```bash
pytest                      # everything except the live test
pytest tests/test_domain.py tests/test_contracts.py   # no Qt, no network needed
```

The suite covers the domain rules, the strict contract, block coverage for every
block size and overlap, document import including a requirement across a page
boundary, partial failures, bounded repair and cancellation, the HTTP adapter
against a mock transport (success, missing key, 401/403, 429 with `Retry-After`,
timeouts, invalid and truncated answers), the layering rules, and an offline
end-to-end run with a fake model. CI needs no paid cloud calls.

The live test is opt-in, because a real call causes real usage:

```bash
SPEC2TEST_LIVE=1 SPEC2TEST_LIVE_MODEL=gpt-oss:120b pytest tests/test_live_cloud.py -v
# local endpoint instead of the cloud:
SPEC2TEST_LIVE=1 SPEC2TEST_LIVE_MODE=ollama_local \
  SPEC2TEST_LIVE_MODEL=gpt-oss:120b-cloud pytest tests/test_live_cloud.py -v
```

No test asserts on the model's wording or expects identical results at
temperature zero.

---

## LLM Pipeline Improvements

* Prompt overrides - choose any prompt file to override the default system prompt from within the GUI.
* Resilient generation - automatic JSON retry plus self-repair of incomplete fields.
* Retrieval augmentation - optional TF-IDF retriever selects the most relevant chunks for each prompt.
* Health checks - the endpoint is probed before generation. A failure is reported; the local `ollama` CLI is never started as a fallback.
* Strict local validation - model answers are checked against a versioned JSON contract, because Ollama Cloud offers no schema-enforced structured outputs.
* Bounded repair - an invalid answer is repaired at most twice with the original requirement, the previous answer and the concrete findings, then rejected visibly.

---

## Notes

* The GUI path assumes an Ollama endpoint reachable over HTTP; the cloud mode is selected in `config.yml` or via the CLI.
* Make sure your LLM supports multi-threading if using the parallel prompts feature.
* Error handling and logging are included to support debugging and tracking.
