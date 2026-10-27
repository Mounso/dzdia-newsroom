from evals.baseline_v1.runner import execute_jobs, plan_jobs
from evals.baseline_v1.schemas import RunRecord, SampleItem
from tests.evals.fakes import FakeTextGenerator

ORIGINAL = SampleItem(
    item_id="tsa-00",
    source="tsa",
    url="https://x.dz/festival-gnaoua/",
    title="t",
    snapshot_id="tsa-00",
)
OPAQUE = ORIGINAL.model_copy(
    update={"item_id": "tsa-00-opaque", "url": "https://x.dz/?p=1", "is_opaque": True}
)
TEXTS = {"tsa-00": "Texte réel de la page."}


def test_plan_jobs_uses_url_or_page_text_and_skips_opaque_page_text() -> None:
    jobs = plan_jobs([ORIGINAL, OPAQUE], TEXTS, ["url_only", "page_text"])

    # original: 2 conditions x (3 summaries + 1 article); opaque: url_only only.
    assert len(jobs) == 8 + 4
    url_only = [j for j in jobs if j.item_id == "tsa-00" and j.condition == "url_only"]
    page_text = [j for j in jobs if j.condition == "page_text"]
    assert all("https://x.dz/festival-gnaoua/" in j.prompt for j in url_only)
    assert all("Texte réel de la page." in j.prompt for j in page_text)
    assert not any(j.item_id == "tsa-00-opaque" and j.condition == "page_text" for j in jobs)
    assert len({j.run_id for j in jobs}) == len(jobs)


async def test_execute_jobs_skips_done_and_records_errors() -> None:
    jobs = plan_jobs([ORIGINAL], TEXTS, ["url_only"])

    def respond(prompt: str) -> str:
        if "Ajoute un titre" in prompt:
            raise RuntimeError("quota exceeded")
        return "Un résumé."

    generator = FakeTextGenerator(respond)
    persisted: list[RunRecord] = []
    records = await execute_jobs(jobs, generator, {jobs[0].run_id}, persisted.append)

    assert len(records) == len(jobs) - 1 == len(persisted)
    assert len(generator.prompts) == len(jobs) - 1
    errors = [r for r in records if r.error]
    assert [r.task for r in errors] == ["article"]
    assert all(r.model == "fake-model" and r.prompt_version == "v1-frozen" for r in records)
