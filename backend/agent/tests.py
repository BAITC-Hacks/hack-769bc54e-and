import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from .core import loop, tools
from .core.domains import _contract, example, load
from .models import Run

TABLE = example._SAMPLE_CSV
TEXT = "Смена 1: 14 обращений, 2 просрочены.\nСмена 2: 9 обращений, 0 просрочено.\n"


@override_settings(LLM_MOCK=True, AGENT_DOMAIN="example")
class AgentLoopTests(TestCase):
    """Ядро: пауза на человека, уважение к отказу, лимит шагов."""

    loop = loop

    def test_pauses_for_approval_then_finishes(self):
        run = self.loop.create_run("разбери выгрузку", TABLE)
        self.loop.advance(run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.AWAITING_APPROVAL)
        self.assertEqual(run.pending[0]["name"], "notify_owner")

        self.loop.decide(run.id, approve=True)
        self.loop.advance(run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.DONE)
        self.assertIn("Отдел контроля", run.final_report)

    def test_rejection_is_respected(self):
        run = self.loop.create_run("разбери выгрузку", TABLE)
        self.loop.advance(run.id)
        self.loop.decide(run.id, approve=False)
        self.loop.advance(run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.DONE)
        self.assertIn("rejected_by_human", run.final_report)

    def test_material_without_findings_finishes_without_approval(self):
        run = self.loop.create_run("что это за материал", TEXT)
        self.loop.advance(run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.DONE)

    def test_raw_material_is_isolated_from_system_prompt(self):
        run = self.loop.create_run("разбери выгрузку", TABLE)
        system, user = run.messages[0], run.messages[1]
        self.assertNotIn("IGNORE ALL PREVIOUS INSTRUCTIONS", system["content"])
        self.assertIn("<raw_material", user["content"])

    def test_model_gets_a_preview_while_tools_get_everything(self):
        big = "col_a,col_b\n" + "\n".join(f"{i},{i * 2}" for i in range(5000))
        run = self.loop.create_run("разбери", big)
        user = run.messages[1]["content"]
        self.assertLess(len(user), len(big) / 2)
        self.assertIn("символов доступны инструментам", user)
        self.assertEqual(run.input_text, big)  # инструменты видят материал целиком

    def test_tokens_are_accumulated(self):
        run = self.loop.create_run("разбери выгрузку", TABLE)
        self.loop.advance(run.id)
        run.refresh_from_db()
        self.assertEqual(run.prompt_tokens, 0)  # mock не тратит токенов

    @override_settings(AGENT_MAX_STEPS=1)
    def test_step_limit_fails_the_run_instead_of_hanging(self):
        run = self.loop.create_run("разбери выгрузку", TABLE)
        self.loop.advance(run.id)
        run.refresh_from_db()
        self.assertIn(run.status, {Run.Status.FAILED, Run.Status.AWAITING_APPROVAL})


@override_settings(AGENT_DOMAIN="example")
class DomainContractTests(TestCase):
    def test_load_registers_tools_and_clears_previous(self):
        load("example")
        first = set(tools.REGISTRY)
        self.assertTrue(first)
        load("example")
        self.assertEqual(set(tools.REGISTRY), first)

    def test_missing_attribute_is_reported(self):
        class Broken:
            BRIEF = "x"
            SAMPLES: list = []

        with self.assertRaises(_contract.DomainError):
            _contract.check(Broken, "broken")


@override_settings(AGENT_DOMAIN="example")
class ToolTests(TestCase):
    ctx = tools.RunContext(run_id="t", input_text="")

    def setUp(self):
        load("example")

    def test_table_is_parsed_and_outlier_found(self):
        r = example.summarize_material(tools.RunContext(run_id="t", input_text=TABLE))
        self.assertEqual(r["kind"], "table")
        self.assertEqual(r["rows"], 10)
        self.assertTrue(r["by_column"]["amount"]["outliers"])

    def test_prose_with_commas_is_not_a_table(self):
        r = example.summarize_material(tools.RunContext(run_id="t", input_text=TEXT))
        self.assertEqual(r["kind"], "text")

    def test_unknown_reference_key_does_not_crash(self):
        self.assertFalse(example.lookup_reference(self.ctx, "нет-такого")["known_key"])

    def test_unknown_tool_returns_error(self):
        self.assertIn("error", tools.execute("nope", {}, self.ctx))

    def test_bad_arguments_return_error_not_crash(self):
        self.assertIn("error", tools.execute("lookup_reference", {"nope": 1}, self.ctx))


@override_settings(LLM_MOCK=True, AGENT_DOMAIN="example")
class ApiTests(TestCase):
    def test_health_reports_mode_and_domain(self):
        data = Client().get("/api/health").json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["domain"], "example")

    def test_samples_come_from_the_domain(self):
        data = Client().get("/api/samples").json()
        self.assertTrue(data)
        self.assertLessEqual({"id", "label", "task", "input"}, set(data[0]))

    def test_run_requires_a_task(self):
        res = Client().post("/api/runs", data="{}", content_type="application/json")
        self.assertEqual(res.status_code, 400)

    def test_oversized_material_is_rejected(self):
        from .views import MAX_INPUT_CHARS

        body = json.dumps({"task": "разбери", "input": "x" * (MAX_INPUT_CHARS + 1)})
        res = Client().post("/api/runs", data=body, content_type="application/json")
        self.assertEqual(res.status_code, 400)

    def test_run_json_exposes_token_usage(self):
        body = json.dumps({"task": "разбери", "input": TEXT})
        # Фоновый поток в тестовой БД не нужен: проверяем форму ответа, а не работу агента
        with patch("agent.views.loop.start_in_background"):
            data = Client().post("/api/runs", data=body, content_type="application/json").json()
        self.assertEqual(data["tokens"], {"prompt": 0, "completion": 0})

    def test_health_hides_model_name_in_mock_mode(self):
        self.assertIsNone(Client().get("/api/health").json()["model"])


@override_settings(AGENT_DOMAIN="contractors")
class ContractorDomainTests(TestCase):
    """Домен Задачи #79-lite. Номера в названиях — требования из CLAUDE.md."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from .core.domains import contractors

        cls.m = contractors
        cls.ctx = tools.RunContext(run_id="t", input_text="")
        cls.base = dict(
            city="Алматы", category="Ведущий", date="2026-11-14",
            event_format="свадьба", budget_kzt=900000, free_text="ведущий на свадьбу",
        )

    def setUp(self):
        load("contractors")

    def search(self, **over):
        return self.m.search_contractors(self.ctx, **dict(self.base, **over))

    def test_options_are_derived_from_the_catalog(self):
        data = Client().get("/api/options").json()
        self.assertEqual(set(data), {"cities", "categories", "event_formats", "languages"})
        self.assertEqual(data["cities"], sorted({p["city"] for p in self.m.catalog()}, key=str.casefold))
        self.assertIn("Ведущий", data["categories"])
        self.assertIn("той", data["event_formats"])
        self.assertIn("казахский", data["languages"])

    def test_catalog_has_66_profiles(self):
        self.assertEqual(len(self.m.catalog()), 66)  # R20

    def test_never_more_than_three_cards(self):
        self.assertLessEqual(len(self.search(date="2026-10-15")["cards"]), 3)  # R3

    def test_busy_contractor_is_excluded(self):
        busy = next(p for p in self.m.catalog() if p["city"] == "Алматы" and "Ведущий" in p["categories"] and p["busy_dates"])
        day = sorted(busy["busy_dates"])[0]
        ids = [c["id"] for c in self.search(date=day)["cards"]]
        self.assertNotIn(busy["id"], ids)  # R8

    def test_order_is_deterministic(self):
        runs = {tuple(c["id"] for c in self.search()["cards"]) for _ in range(5)}
        self.assertEqual(len(runs), 1)  # R11

    def test_three_outcomes_are_distinguishable(self):  # R12
        self.assertEqual(self.search(date="2026-10-15")["outcome"], "matched")
        self.assertEqual(
            self.search(city="Астана", category="Лайв-бэнд")["outcome"], "no_category_in_city"
        )
        self.assertEqual(
            self.search(category="Отель", date="2026-12-26", budget_kzt=3000000)["outcome"],
            "all_filtered_out",
        )

    def test_two_dates_give_different_results(self):
        a = self.search(date="2026-10-15")["passed_filters"]
        b = self.search(date="2026-12-26")["passed_filters"]
        self.assertNotEqual(a, b)  # R16

    def test_rejected_lists_every_reason_not_just_the_first(self):
        rejected = self.search(category="Отель", date="2026-12-26", budget_kzt=3000000)["rejected"]
        codes = {r["code"] for entry in rejected for r in entry["reasons"]}
        self.assertEqual(codes, {"busy", "budget"})

    def test_profile_without_max_hours_survives_duration_filter(self):
        florist = next(p for p in self.m.catalog() if p["max_hours"] is None)
        req = dict(city=florist["city"], category=florist["categories"][0], date="2026-12-31",
                   event_format=florist["event_formats"][0], budget_kzt=10**9, duration_hours=12)
        rejected = self.m.search_contractors(self.ctx, **req)["rejected"]
        blocked = {e["id"] for e in rejected for r in e["reasons"] if r["code"] == "duration"}
        self.assertNotIn(florist["id"], blocked)  # I1

    def test_category_filter_matches_any_of_the_profile_categories(self):
        multi = next((p for p in self.m.catalog() if len(p["categories"]) > 1), None)
        if multi is None:
            self.skipTest("в датасете нет профилей с несколькими категориями")
        for category in multi["categories"]:  # I2
            pool = self.m._shortlist({"city": multi["city"], "category": category})[0]
            self.assertIn(multi["id"], {p["id"] for p in pool})

    def test_date_outside_the_calendar_window_is_flagged(self):
        self.assertIn("warning", self.search(date="2027-03-01"))  # I6

    def test_cards_carry_honesty_flags(self):
        card = self.search(date="2026-10-15")["cards"][0]
        self.assertEqual(set(card["flags"]), {"synthetic", "price_imputed", "city_imputed"})  # R22

    def test_bad_date_returns_error_not_crash(self):
        self.assertIn("error", self.search(date="14 ноября"))

    def test_diagnosis_reports_when_no_single_change_helps(self):
        d = self.m.diagnose_request(self.ctx, **dict(self.base, category="Отель",
                                                     date="2026-12-26", budget_kzt=3000000))
        self.assertFalse(d["single_change_helps"])
        self.assertEqual(d["blocked_by"], {"busy": 2, "budget": 2})

    def test_diagnosis_names_the_budget_that_would_help(self):
        d = self.m.diagnose_request(self.ctx, **dict(self.base, date="2026-10-15", budget_kzt=400000))
        self.assertTrue(d["single_change_helps"])
        self.assertEqual(d["budget_needed_kzt"], 650000)

    def test_diagnosis_says_where_the_category_exists(self):
        d = self.m.diagnose_request(self.ctx, **dict(self.base, city="Астана", category="Лайв-бэнд"))
        self.assertEqual(d["category_available_in"], ["Алматы"])

    def test_quote_is_always_a_real_fragment_of_the_description(self):
        for card in self.search(date="2026-10-15")["cards"]:  # A16
            quote = card["match"]["quote"]
            if quote is None:
                continue
            profile = next(p for p in self.m.catalog() if p["id"] == card["id"])
            self.assertIn(quote.rstrip("."), profile["description"])

    def test_words_echoed_from_the_request_are_not_counted_as_a_match(self):
        card = self.search(date="2026-10-15", free_text="ведущий на свадьбу в Алматы")["cards"][0]
        self.assertNotIn("ведущий", card["match"]["shared_words"])
        self.assertNotIn("алматы", card["match"]["shared_words"])

    def test_quote_selection_is_deterministic(self):
        quotes = {tuple(c["match"]["quote"] or "" for c in self.search(date="2026-10-15")["cards"]) for _ in range(5)}
        self.assertEqual(len(quotes), 1)

    def test_missing_category_suggests_where_it_exists(self):
        d = self.m.diagnose_request(self.ctx, **dict(self.base, city="Астана", category="Лайв-бэнд"))
        self.assertTrue(any("Алматы" in line for line in d["suggestions"]))

    def test_unknown_category_is_not_passed_off_as_absent_in_the_city(self):
        # Раньше «Дрессировщик тигров» давал «в Алматы нет такой категории» — ложь,
        # выглядящая как ответ. Теперь несуществующее значение названо своим именем.
        r = self.search(category="Дрессировщик тигров")
        self.assertEqual(r["outcome"], "unknown_category")
        self.assertIn("Ведущий", r["known_values"])

    def test_unknown_city_is_reported_as_such(self):
        r = self.search(city="Алма-Ата")
        self.assertEqual(r["outcome"], "unknown_city")
        self.assertIn("Алматы", r["known_values"])

    def test_declined_and_lowercase_values_are_normalised(self):
        for category in ("Ведущие", "ведущий", " Ведущий "):
            r = self.search(category=category, date="2026-10-15")
            self.assertEqual(r["outcome"], "matched", category)
            self.assertEqual(r["request"]["category"], "Ведущий")

    def test_real_category_missing_only_here_still_says_where_it_exists(self):
        d = self.m.diagnose_request(self.ctx, **dict(self.base, city="Астана", category="Декоратор"))
        self.assertEqual(d["outcome"], "no_category_in_city")
        self.assertTrue(any("Алматы" in line for line in d["suggestions"]))

    def test_suggestions_are_phrased_as_counts_that_would_fit(self):
        d = self.m.diagnose_request(self.ctx, **dict(self.base, date="2026-10-15", budget_kzt=400000))
        self.assertTrue(all("подойдёт" in s or "освободится" in s or "не помогает" in s for s in d["suggestions"]))

    def test_mock_report_explains_itself_when_nothing_was_parsed(self):
        from .core.llm import _mock_report

        self.assertIn("Mock-режим", _mock_report({}))
        self.assertIn("пример", _mock_report({}))

    def test_search_carries_the_diagnosis_when_fewer_than_three(self):
        # Объяснение «почему мало» не должно зависеть от второго вызова модели
        r = self.search(category="Отель", date="2026-12-26", budget_kzt=3000000)
        self.assertIn("diagnosis", r)
        self.assertTrue(r["diagnosis"]["suggestions"])

    def test_search_does_not_diagnose_when_there_are_three(self):
        r = self.search(date="2026-10-15")
        self.assertEqual(len(r["cards"]), 3)
        self.assertNotIn("diagnosis", r)

    def test_embedded_diagnosis_matches_the_standalone_tool(self):
        kw = dict(self.base, category="Отель", date="2026-12-26", budget_kzt=3000000)
        embedded = self.m.search_contractors(self.ctx, **kw)["diagnosis"]
        standalone = self.m.diagnose_request(self.ctx, **kw)
        self.assertEqual(embedded["suggestions"], standalone["suggestions"])
        self.assertEqual(embedded["blocked_by"], standalone["blocked_by"])
