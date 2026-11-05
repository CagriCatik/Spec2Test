"""20 requirements, with and without retrieval context, two runs each."""
import json, random, sys, time
from pathlib import Path
sys.path.insert(0, "/Users/cc/Desktop/spec2test/src")

from spec2test.adapters.documents import DEFAULT_IMPORTERS
from spec2test.adapters.knowledge import SqliteKnowledgeIndex
from spec2test.adapters.ollama.client import OllamaChatClient, OllamaSettings
from spec2test.adapters.persistence import JsonRunStore
from spec2test.application.generate import GenerationSettings, generate_drafts
from spec2test.application.indexing import collect_documents, sync_index

ROOT = Path("/Users/cc/Desktop/spec2test")
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

index = SqliteKnowledgeIndex(OUT / "knowledge.db")
sync = sync_index(index, collect_documents(ROOT / "requirements"), DEFAULT_IMPORTERS)
requirements = sync.requirements
documents = sync.documents
print(f"index: {sync.summary()}", flush=True)

# 20 requirements spread over the documents: one per document, then fill up
by_document: dict[str, list] = {}
for r in requirements:
    if r.external_id:
        by_document.setdefault(r.spans[0].document_id, []).append(r)
random.seed(20261105)
chosen = []
for group in sorted(by_document.values(), key=lambda g: g[0].spans[0].document_id):
    chosen.append(group[0])
chosen = chosen[:20]
print(f"selected {len(chosen)} requirements: "
      f"{[r.external_id for r in chosen]}", flush=True)

def model():
    return OllamaChatClient(OllamaSettings(
        model="gpt-oss:120b-cloud", mode="ollama_local",
        host="http://localhost:11434", read_timeout_s=300, max_retries=1))

rows = []
store = JsonRunStore(OUT / "runs")
for use_retrieval in (False, True):
    for repetition in (1, 2):
        for requirement in chosen:
            started = time.perf_counter()
            run = generate_drafts(
                [requirement], model(),
                settings=GenerationSettings(
                    use_reference_context=True, use_retrieval=use_retrieval,
                    max_cases_per_unit=2, max_repair_attempts=1,
                    job_timeout_s=420),
                documents=documents, catalogue=requirements,
                knowledge=index, log=lambda m: None)
            store.save_run(run)
            codes = [i.code for u in run.units for i in u.issues]
            rows.append(dict(
                requirement=requirement.external_id, retrieval=use_retrieval,
                repetition=repetition, status=run.status.value,
                drafts=len(run.test_cases), questions=len(run.open_questions),
                unsupported=codes.count("C_UNSUPPORTED_VALUE"),
                from_context=codes.count("C_VALUE_FROM_CONTEXT"),
                no_criterion=codes.count("C_NO_MEASURABLE_CRITERION"),
                conflicts=codes.count("C_POSSIBLE_VALUE_CONFLICT"),
                context_items=len(run.context_items),
                model_calls=sum(len(u.attempts) for u in run.units),
                repairs=sum(1 for u in run.units for a in u.attempts if a.is_repair),
                seconds=round(time.perf_counter() - started, 1),
                run_id=run.run_id))
            (OUT / "rows.json").write_text(json.dumps(rows, indent=2))
            print(f"  retrieval={use_retrieval} rep{repetition} "
                  f"{requirement.external_id}: {rows[-1]['status']} "
                  f"drafts={rows[-1]['drafts']} q={rows[-1]['questions']} "
                  f"unsup={rows[-1]['unsupported']} ctx={rows[-1]['context_items']} "
                  f"{rows[-1]['seconds']}s", flush=True)
print("DONE", len(rows), "runs", flush=True)
