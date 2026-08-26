from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.delivery_bundle import scaffold_project
from orchestrator.governance import GovernanceEngine, integration_blueprint
from tests.helpers import make_git_repo


def technology_research_for(engine, race_recommended=False):
    common_source = {
        "accessed_at": "2026-08-25",
        "claims": ["Supports one bounded technical comparison claim"],
        "quality_signals": ["Named publisher and dated evidence"],
        "limitations": ["Does not prove repository-specific integration alone"],
    }
    sources = [
        {
            **common_source,
            "id": "community-1",
            "channel": "community",
            "kind": "forum_thread",
            "title": "Recent practitioner discussion",
            "url": "https://community.example.com/discussions/2026-workflows",
            "publisher": "Example community",
            "published_at": "2026-03-01",
            "summary": "Practitioners compare bounded workflows in production-like use.",
        },
        {
            **common_source,
            "id": "community-2",
            "channel": "community",
            "kind": "community_case_study",
            "title": "Independent implementation case",
            "url": "https://engineering.example.org/cases/agent-delivery",
            "publisher": "Engineering example",
            "published_at": "2025-10-01",
            "summary": "A separate team documents integration and maintenance tradeoffs.",
        },
        {
            **common_source,
            "id": "academic-1",
            "channel": "academic",
            "kind": "peer_reviewed_paper",
            "title": "Recent controlled study of software agents",
            "url": "https://doi.org/10.0000/example.2025.1",
            "publisher": "Example scholarly society",
            "published_at": "2025-06-01",
            "summary": "A controlled comparison reports strengths and failure modes.",
            "peer_reviewed": True,
            "venue": "Example Software Engineering Conference",
            "identifier": "doi:10.0000/example.2025.1",
            "methods_summary": "Controlled tasks with fixed inputs, metrics, and blinded scoring.",
        },
        {
            **common_source,
            "id": "academic-2",
            "channel": "academic",
            "kind": "preprint",
            "title": "Replication study for agent evaluation",
            "url": "https://arxiv.org/abs/2601.00001",
            "publisher": "arXiv",
            "published_at": "2026-01-15",
            "summary": "A replication discusses benchmark leakage and cost controls.",
            "venue": "arXiv",
            "identifier": "arXiv:2601.00001",
            "methods_summary": "Replicated fixed-task evaluation across isolated contexts.",
        },
        {
            **common_source,
            "id": "repo-a",
            "channel": "open_source",
            "kind": "repository",
            "title": "Framework A repository",
            "url": "https://github.com/example/framework-a",
            "publisher": "Framework A maintainers",
            "published_at": "2026-07-01",
            "summary": "Repository activity, license, issues, and release history for A.",
            "primary_evidence": True,
        },
        {
            **common_source,
            "id": "repo-b",
            "channel": "open_source",
            "kind": "repository",
            "title": "Framework B repository",
            "url": "https://gitlab.com/example/framework-b",
            "publisher": "Framework B maintainers",
            "published_at": "2026-06-01",
            "summary": "Repository activity, license, issues, and release history for B.",
            "primary_evidence": True,
        },
        {
            **common_source,
            "id": "docs-a",
            "channel": "official",
            "kind": "official_documentation",
            "title": "Framework A documentation",
            "url": "https://framework-a.example.com/docs",
            "publisher": "Framework A maintainers",
            "published_at": "2026-07-01",
            "summary": "Official integration, security, and extension documentation for A.",
            "primary_evidence": True,
        },
        {
            **common_source,
            "id": "docs-b",
            "channel": "official",
            "kind": "official_documentation",
            "title": "Framework B documentation",
            "url": "https://framework-b.example.net/docs",
            "publisher": "Framework B maintainers",
            "published_at": "2026-06-01",
            "summary": "Official integration, security, and extension documentation for B.",
            "primary_evidence": True,
        },
    ]
    scores_a = {name: 4 for name in (
        "requirements_fit", "maturity", "maintenance", "security",
        "integration_fit", "extensibility", "ecosystem", "license_fit",
    )}
    scores_b = dict(scores_a, integration_fit=3, extensibility=5)
    return engine.compile_technology_research(
        {
            "research_question": "Which bounded architecture best fits the confirmed delivery scope?",
            "human_scope": ["Keep the human-confirmed goal, non-goals, and risk boundary unchanged"],
            "as_of": "2026-08-25",
            "queries": {
                "community": ["recent practitioner experience and failure modes"],
                "academic": ["recent peer reviewed agent workflow evaluation"],
                "open_source": ["maintained open source workflow frameworks"],
                "official": ["official architecture security license documentation"],
            },
            "sources": sources,
            "framework_candidates": [
                {
                    "id": "framework-a",
                    "name": "Framework A",
                    "repository_url": "https://github.com/example/framework-a",
                    "official_docs_url": "https://framework-a.example.com/docs",
                    "license": "Apache-2.0",
                    "status": "maintained",
                    "latest_release_at": "2026-07-01",
                    "source_ids": ["repo-a", "docs-a"],
                    "strengths": ["Simple integration"],
                    "gaps": ["Less flexible extension model"],
                    "risks": ["Repository-specific fit remains unproven"],
                    "integration_notes": "Fits a thin stateless governance adapter.",
                    "scores": scores_a,
                },
                {
                    "id": "framework-b",
                    "name": "Framework B",
                    "repository_url": "https://gitlab.com/example/framework-b",
                    "official_docs_url": "https://framework-b.example.net/docs",
                    "license": "MIT",
                    "status": "maintained",
                    "latest_release_at": "2026-06-01",
                    "source_ids": ["repo-b", "docs-b"],
                    "strengths": ["Flexible extension model"],
                    "gaps": ["Higher integration cost"],
                    "risks": ["More configuration surface"],
                    "integration_notes": "Potentially stronger for complex projects after a spike.",
                    "scores": scores_b,
                },
            ],
            "technology_paths": [
                {
                    "id": "path-a",
                    "name": "Thin Framework A adapter",
                    "approach": "Use Framework A through a stateless adapter.",
                    "framework_ids": ["framework-a"],
                    "hypothesis": "Lower integration complexity will win for this scope.",
                    "unknowns": ["Repository-specific recovery behavior needs a prototype"],
                    "strengths": ["Lower integration cost"],
                    "risks": ["May constrain later extension"],
                    "source_ids": ["community-1", "academic-1", "repo-a", "docs-a"],
                    "estimated_effort": "One bounded implementation slice",
                },
                {
                    "id": "path-b",
                    "name": "Extensible Framework B adapter",
                    "approach": "Use Framework B with an explicit extension layer.",
                    "framework_ids": ["framework-b"],
                    "hypothesis": "Extension flexibility may justify its integration cost.",
                    "unknowns": ["Real task latency and configuration burden need a prototype"],
                    "strengths": ["More extension points"],
                    "risks": ["Higher setup and maintenance cost"],
                    "source_ids": ["community-2", "academic-2", "repo-b", "docs-b"],
                    "estimated_effort": "One bounded implementation slice",
                },
            ],
            "recommendation": {
                "selected_path_ids": ["path-a", "path-b"] if race_recommended else ["path-a"],
                "rationale": "A is the default; a bounded race is justified only when the human accepts unresolved repository fit.",
                "source_ids": ["community-1", "academic-1", "repo-a", "docs-a"],
                "key_tradeoffs": ["Integration simplicity versus extension flexibility"],
                "rejected_alternatives": ["Unbounded parallel development"],
                "confidence": 0.82,
                "race_recommended": race_recommended,
                "race_rationale": "Both paths remain viable and recovery/cost unknowns require a prototype." if race_recommended else "",
            },
            "review_declaration": {
                "context_id": "fresh-research-review",
                "fresh_context": True,
                "read_only": True,
                "collector_transcript_visible": False,
                "candidate_implementation_visible": False,
            },
            "review_findings": [],
            "review_verdict": "PASS",
        }
    )


def intent_alignment_for(source, bounded_race=False):
    engine = GovernanceEngine()
    research = technology_research_for(engine, race_recommended=bounded_race)
    outcomes = list(source.get("outcomes") or ["The requested result is observable"])
    brief = engine.compile_intent_brief(
        {
            "original_request": str(source.get("goal") or "Build the requested change"),
            "conversation_refs": ["test-fixture://original-request"],
            "expected_outcomes": outcomes,
            "acceptance_examples": [
                {
                    "id": "example-1",
                    "input": "A valid representative user input",
                    "expected_output": str(
                        (source.get("acceptance_criteria") or [outcomes[0]])[0]
                    ),
                }
            ],
            "development_executor": {
                "provider": "Codex",
                "model": "locally configured",
                "authentication": "locally authenticated subscription",
                "purpose": "Investigate, implement, and run repository checks",
            },
            "product_runtime": {
                "provider": "none",
                "model": "not_applicable",
                "authentication": "not_applicable",
                "purpose": "This fixture declares no model API in the delivered product",
            },
            "technical_choices": [
                {
                    "id": "delivery-shape",
                    "topic": "Delivery shape",
                    "selected": "Implement the compiled work contract",
                    "alternatives": ["Documentation-only response"],
                    "rationale": "The fixture represents a product-code delivery",
                    "evidence": "Test fixture declaration",
                    "research_hash": research["research_hash"],
                    "high_impact": True,
                }
            ],
            "technology_research": research,
            "technology_strategy": (
                {
                    "mode": "bounded_race",
                    "selected_path_ids": ["path-a", "path-b"],
                    "decision_rationale": "The human authorizes a bounded comparison of two viable paths.",
                    "common_test_commands": ["python3 -m unittest"],
                    "evaluation_dimensions": ["quality", "performance", "cost", "risk"],
                    "time_budget_minutes": 90,
                    "cost_budget": "No external paid calls",
                    "fusion_allowed": True,
                    "stop_conditions": ["Stop at 90 minutes", "Reject paths with failing common tests"],
                }
                if bounded_race
                else {
                    "mode": "single_path",
                    "selected_path_ids": ["path-a"],
                    "decision_rationale": "The default fixture does not need an implementation race.",
                }
            ),
            "non_goals": list(source.get("non_goals") or ["No adjacent work"]),
            "risk_boundaries": list(
                source.get("human_decisions") or ["A human approves external actions"]
            ),
            "research_refs": ["test-fixture://research"],
            "unresolved_questions": [],
        }
    )
    coverage = [
        {
            "requirement_id": "outcome-%d" % (index + 1),
            "status": "covered",
            "evidence": "Mapped to outcomes[%d]" % index,
        }
        for index, _item in enumerate(brief["expected_outcomes"])
    ]
    coverage.extend(
        [
            {
                "requirement_id": "example-1",
                "status": "covered",
                "evidence": "Mapped to acceptance_criteria[0]",
            },
            {
                "requirement_id": "delivery-shape",
                "status": "covered",
                "evidence": "Bound in the intent brief carried by the contract",
            },
            {
                "requirement_id": "development-executor",
                "status": "covered",
                "evidence": "Codex is declared only as the development executor",
            },
            {
                "requirement_id": "product-runtime",
                "status": "covered",
                "evidence": "The product runtime is separately and explicitly declared",
            },
            {
                "requirement_id": "research-recommendation",
                "status": "covered",
                "evidence": "The technical choice binds the frozen research hash",
            },
            {
                "requirement_id": "technology-strategy",
                "status": "covered",
                "evidence": "The human-facing brief selects one researched path",
            },
        ]
    )
    proposed = {key: value for key, value in source.items() if key != "intent_alignment"}
    inspection = engine.compile_intent_inspection(
        {
            "brief": brief,
            "proposed_contract_source": proposed,
            "technology_research": research,
            "research_evidence": ["Fixture choices were compared with the contract source"],
            "evidence_inputs": [
                "original_request",
                "intent_brief",
                "technical_research",
                "proposed_contract",
                "acceptance_examples",
            ],
            "inspector_declaration": {
                "context_id": "fresh-intent-fixture",
                "fresh_context": True,
                "read_only": True,
                "owner_transcript_visible": False,
                "peer_findings_visible": False,
            },
            "coverage": coverage,
            "findings": [],
            "verdict": "PASS",
        }
    )
    return {"brief": brief, "inspection": inspection}


def ready_source(**overrides):
    value = {
        "title": "Add account settings",
        "goal": "Let a signed-in user update account settings",
        "users": ["Signed-in customer"],
        "outcomes": ["A saved setting is visible after reload"],
        "acceptance_criteria": ["Saving a valid setting persists it and shows confirmation"],
        "non_goals": ["No administrator bulk editing"],
        "constraints": ["Keep the public API compatible"],
        "forbidden_behaviors": ["Do not expose another tenant's settings"],
        "human_decisions": ["A human approves the final merge"],
        "deterministic_checks": ["python3 -m unittest"],
        "change_types": ["product_code", "ui"],
        "risk_flags": ["authorization", "multi_tenant", "user_experience"],
    }
    value.update(overrides)
    if "intent_alignment" not in overrides:
        value["intent_alignment"] = intent_alignment_for(value)
    return value


def research_source_from(artifact):
    return {
        "research_question": copy.deepcopy(artifact["research_question"]),
        "human_scope": copy.deepcopy(artifact["human_scope"]),
        "as_of": artifact["as_of"],
        "queries": copy.deepcopy(artifact["queries"]),
        "sources": copy.deepcopy(artifact["sources"]),
        "framework_candidates": copy.deepcopy(artifact["framework_candidates"]),
        "technology_paths": copy.deepcopy(artifact["technology_paths"]),
        "recommendation": copy.deepcopy(artifact["recommendation"]),
        "review_declaration": copy.deepcopy(artifact["review_declaration"]),
        "review_findings": copy.deepcopy(artifact["review_findings"]),
        "review_verdict": artifact["requested_verdict"],
    }


class TechnologyResearchTests(unittest.TestCase):
    def setUp(self):
        self.engine = GovernanceEngine()
        self.valid = technology_research_for(self.engine, race_recommended=True)

    def compile_mutation(self, mutate):
        source = research_source_from(self.valid)
        mutate(source)
        return self.engine.compile_technology_research(source)

    def test_four_channel_research_and_independent_quality_review_pass(self):
        self.assertEqual(self.valid["status"], "pass")
        self.assertEqual(
            {item["display_name"] for item in self.valid["roles"]},
            {"技术调研员", "调研质检员"},
        )
        self.assertEqual(len(self.valid["framework_candidates"]), 2)
        self.assertEqual(len(self.valid["technology_paths"]), 2)

    def test_one_community_domain_is_not_enough(self):
        result = self.compile_mutation(
            lambda source: source.update(
                sources=[
                    item
                    for item in source["sources"]
                    if item["id"] != "community-2"
                ]
            )
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("community-coverage", {item["id"] for item in result["blockers"]})

    def test_stale_academic_claim_requires_recent_corroboration(self):
        def mutate(source):
            paper = next(item for item in source["sources"] if item["id"] == "academic-2")
            paper["published_at"] = "2010-01-01"
            paper["foundational"] = False
            paper["corroborates"] = []

        result = self.compile_mutation(mutate)
        self.assertIn(
            "stale-academic-academic-2", {item["id"] for item in result["blockers"]}
        )

    def test_one_framework_or_incomplete_fit_matrix_is_blocked(self):
        one = self.compile_mutation(
            lambda source: source.update(
                framework_candidates=source["framework_candidates"][:1]
            )
        )
        self.assertIn("framework-candidates", {item["id"] for item in one["blockers"]})

        def remove_license_score(source):
            source["framework_candidates"][0]["scores"].pop("license_fit")

        incomplete = self.compile_mutation(remove_license_score)
        self.assertIn(
            "framework-scores-framework-a",
            {item["id"] for item in incomplete["blockers"]},
        )

    def test_research_reviewer_cannot_see_collector_transcript(self):
        result = self.compile_mutation(
            lambda source: source["review_declaration"].update(
                collector_transcript_visible=True
            )
        )
        self.assertIn(
            "research-review-leakage", {item["id"] for item in result["blockers"]}
        )

    def test_human_strategy_cannot_start_more_than_three_race_paths(self):
        research_source = research_source_from(self.valid)
        for suffix in ("c", "d"):
            extra = copy.deepcopy(research_source["technology_paths"][0])
            extra["id"] = "path-%s" % suffix
            extra["name"] = "Additional path %s" % suffix.upper()
            research_source["technology_paths"].append(extra)
        research = self.engine.compile_technology_research(research_source)
        fixture = ready_source()["intent_alignment"]["brief"]
        brief_source = {
            key: copy.deepcopy(fixture[key])
            for key in (
                "original_request",
                "conversation_refs",
                "expected_outcomes",
                "acceptance_examples",
                "development_executor",
                "product_runtime",
                "technical_choices",
                "non_goals",
                "risk_boundaries",
                "research_refs",
                "unresolved_questions",
            )
        }
        for choice in brief_source["technical_choices"]:
            choice["research_hash"] = research["research_hash"]
        brief_source["technology_research"] = research
        brief_source["technology_strategy"] = {
            "mode": "bounded_race",
            "selected_path_ids": ["path-a", "path-b", "path-c", "path-d"],
            "decision_rationale": "Try every path",
            "common_test_commands": ["python3 -m unittest"],
            "evaluation_dimensions": ["quality"],
            "time_budget_minutes": 100,
            "cost_budget": "No paid calls",
            "fusion_allowed": True,
            "stop_conditions": ["Stop at the budget"],
        }
        brief = self.engine.compile_intent_brief(brief_source)
        self.assertIn(
            "race_path_count", {item["id"] for item in brief["confirmation_questions"]}
        )


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.engine = GovernanceEngine()

    def test_intent_brief_blocks_without_input_output_example(self):
        brief = self.engine.compile_intent_brief(
            {
                "original_request": "Build an amount calculator",
                "expected_outcomes": ["The user receives a calculated amount"],
                "development_executor": {
                    "provider": "Codex",
                    "purpose": "Develop the product",
                },
                "product_runtime": {
                    "provider": "DeepSeek official API",
                    "purpose": "Extract structured candidate facts",
                },
                "non_goals": ["No external email sending"],
                "risk_boundaries": ["A human approves paid API execution"],
            }
        )

        self.assertEqual(brief["status"], "needs_clarification")
        self.assertIn(
            "acceptance_examples",
            {item["id"] for item in brief["confirmation_questions"]},
        )

    def test_intent_inspector_blocks_deepseek_amount_goal_substitution(self):
        brief = self.engine.compile_intent_brief(
            {
                "original_request": "Use DeepSeek in the product demo and calculate an amount",
                "expected_outcomes": ["Return an explicit calculated EUR amount"],
                "acceptance_examples": [
                    {
                        "id": "amount-example",
                        "input": "Approved fixture with net 100 and VAT 19 percent",
                        "expected_output": "Total 119.00 EUR",
                    }
                ],
                "development_executor": {
                    "provider": "Codex",
                    "purpose": "Develop and test the repository",
                },
                "product_runtime": {
                    "provider": "DeepSeek official API",
                    "model": "deepseek-chat",
                    "authentication": "DEEPSEEK_API_KEY environment variable",
                    "purpose": "Extract facts for the product demo",
                },
                "technical_choices": [
                    {
                        "id": "money-core",
                        "topic": "Amount calculation",
                        "selected": "Deterministic Decimal calculator",
                        "alternatives": ["Do not calculate an amount"],
                        "rationale": "The user explicitly requires a numeric result",
                        "high_impact": True,
                    }
                ],
                "non_goals": ["No real invoice sending"],
                "risk_boundaries": ["Never expose the API key"],
            }
        )
        inspection = self.engine.compile_intent_inspection(
            {
                "brief": brief,
                "proposed_contract_source": {
                    "goal": "Use the locally logged-in Codex CLI and show rules without calculating money"
                },
                "research_evidence": ["Compared the proposed runtime with the original request"],
                "evidence_inputs": [
                    "original_request",
                    "intent_brief",
                    "technical_research",
                    "proposed_contract",
                    "acceptance_examples",
                ],
                "inspector_declaration": {
                    "context_id": "fresh-read-only-inspector",
                    "fresh_context": True,
                    "read_only": True,
                    "owner_transcript_visible": False,
                    "peer_findings_visible": False,
                },
                "coverage": [
                    {
                        "requirement_id": "outcome-1",
                        "status": "changed",
                        "evidence": "The proposed contract explicitly refuses to calculate money",
                    },
                    {
                        "requirement_id": "amount-example",
                        "status": "missing",
                        "evidence": "No numeric amount example remains",
                    },
                    {
                        "requirement_id": "money-core",
                        "status": "changed",
                        "evidence": "The deterministic calculator was removed",
                    },
                    {
                        "requirement_id": "development-executor",
                        "status": "covered",
                        "evidence": "Codex remains the development executor",
                    },
                    {
                        "requirement_id": "product-runtime",
                        "status": "changed",
                        "evidence": "Codex login was substituted for the DeepSeek product runtime",
                    },
                ],
                "findings": [
                    {
                        "id": "provider-confusion",
                        "category": "provider_confusion",
                        "status": "blocking",
                        "title": "开发执行器被偷换成产品运行时",
                        "evidence": "原意图要求 DeepSeek API；拟定契约只保留 Codex CLI",
                        "question_for_human": "是否仍要求产品 Demo 使用 DeepSeek API 并输出金额？",
                    }
                ],
                "verdict": "BLOCKED",
            }
        )

        self.assertEqual(inspection["status"], "blocked")
        self.assertIn("provider_confusion", {item["category"] for item in inspection["blockers"]})
        self.assertIn("是否仍要求", "；".join(inspection["human_questions"]))

    def test_handoff_names_both_intent_roles_and_prohibits_owner_creation(self):
        contract = self.engine.compile_contract(ready_source())
        handoff = self.engine.delivery_handoff(contract, self.engine.route(contract))

        self.assertEqual(handoff["status"], "awaiting_intent_attestation")
        self.assertFalse(handoff["owner_task"]["creation_allowed"])
        self.assertEqual(
            {item["display_name"] for item in handoff["intent_gate"]["tasks"]},
            {"意图确认员", "意图检查员"},
        )

    def test_contract_change_after_intent_inspection_requires_reinspection(self):
        source = ready_source()
        source["goal"] = "Silently changed after the independent intent inspection"
        contract = self.engine.compile_contract(source)

        self.assertEqual(contract["status"], "needs_clarification")
        self.assertIn(
            "intent_contract_drift",
            {item["id"] for item in contract["clarifications"]},
        )

    def test_low_risk_documentation_records_intent_exemption(self):
        contract = self.engine.compile_contract(
            {
                "goal": "Correct a documentation typo",
                "users": ["Documentation reader"],
                "outcomes": ["The corrected word is visible"],
                "acceptance_criteria": ["The page contains the corrected word"],
                "non_goals": ["No product behavior changes"],
                "deterministic_checks": ["python3 -m unittest"],
                "change_types": ["documentation"],
            }
        )
        plan = self.engine.route(contract)
        handoff = self.engine.delivery_handoff(contract, plan)

        self.assertEqual(contract["status"], "ready")
        self.assertFalse(contract["intent_alignment"]["required"])
        self.assertTrue(contract["intent_alignment"]["exemption_reason"])
        self.assertEqual(handoff["status"], "ready_for_control_plane")
        self.assertTrue(handoff["owner_task"]["creation_allowed"])

    def test_missing_oracle_and_boundaries_require_clarification(self):
        contract = self.engine.compile_contract({"goal": "Build it"})

        self.assertEqual(contract["status"], "needs_clarification")
        self.assertEqual(
            {item["id"] for item in contract["clarifications"]},
            {
                "intent_brief",
                "intent_inspection",
                "target_users",
                "observable_outcome",
                "acceptance_criteria",
                "non_goals",
                "deterministic_evidence",
            },
        )
        for question in contract["question_gate"]["blocking_questions"]:
            self.assertEqual(question["decision_id"], question["id"])
            self.assertEqual(question["impact"], "high")
            self.assertIn("decision_owner", question)
            self.assertIn("reversible", question)
            self.assertIn("consequence", question)

    def test_contract_hash_is_stable_and_changes_with_intent(self):
        first = self.engine.compile_contract(ready_source())
        same = self.engine.compile_contract(ready_source())
        changed = self.engine.compile_contract(
            ready_source(acceptance_criteria=["A different observable result"])
        )

        self.assertEqual(first["status"], "ready")
        self.assertEqual(first["contract_hash"], same["contract_hash"])
        self.assertNotEqual(first["contract_hash"], changed["contract_hash"])

    def test_risk_inference_is_a_signal_and_explicit_unknown_flags_fail(self):
        source = ready_source(risk_flags=[], goal="Change OAuth login and billing migration")
        contract = self.engine.compile_contract(source)

        self.assertEqual(contract["risk"]["level"], "high")
        self.assertIn("authentication", contract["risk"]["inferred_flags"])
        self.assertIn("billing", contract["risk"]["inferred_flags"])

        with self.assertRaisesRegex(ValueError, "unknown risk_flags"):
            self.engine.compile_contract(ready_source(risk_flags=["made_up_risk"]))

    def test_negative_guardrails_do_not_expand_inferred_risk(self):
        contract = self.engine.compile_contract(
            ready_source(
                goal="Build a legal billing review UI with a stable public API",
                outcomes=["A reviewer can inspect calculated billing entries"],
                acceptance_criteria=["The public API returns the documented billing result"],
                constraints=["Do not add a model API key or expose credentials"],
                forbidden_behaviors=["Do not deploy or release to production"],
                change_types=["product_code", "ui"],
                risk_flags=["billing", "privacy", "public_api", "user_experience"],
            )
        )

        self.assertEqual(contract["risk"]["level"], "high")
        self.assertEqual(
            contract["risk"]["flags"],
            ["billing", "privacy", "public_api", "user_experience"],
        )
        self.assertNotIn("production_release", contract["risk"]["inferred_flags"])
        self.assertNotIn("secrets", contract["risk"]["inferred_flags"])

    def test_compiled_contract_rejects_post_hash_intent_or_risk_changes(self):
        contract = self.engine.compile_contract(ready_source())
        changed_intent = dict(contract, goal="Silently changed goal")
        changed_risk = dict(contract, risk={**contract["risk"], "level": "low"})

        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            self.engine.route(changed_intent)
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            self.engine.route(changed_risk)

    def test_question_gate_interrupts_only_for_policy_and_domain_uncertainty(self):
        contract = self.engine.compile_contract(
            ready_source(
                uncertainties=[
                    {
                        "id": "policy",
                        "category": "policy_choice",
                        "statement": "Choose whether legacy clients remain supported",
                        "question": "Must legacy clients remain supported?",
                        "status": "unresolved",
                    },
                    {
                        "id": "invariant",
                        "category": "engineering_invariant",
                        "statement": "The real Git diff root must match the assigned root",
                        "status": "unresolved",
                    },
                    {
                        "id": "research",
                        "category": "researchable_fact",
                        "statement": "The installed Kandev profile shape must be discovered",
                        "status": "unresolved",
                    },
                ]
            )
        )

        self.assertEqual(contract["status"], "needs_clarification")
        self.assertEqual([item["id"] for item in contract["clarifications"]], ["policy"])
        routes = {
            item["id"]: item["route"]
            for item in contract["question_gate"]["non_blocking_routes"]
        }
        self.assertEqual(routes["invariant"], "prove_from_repository")
        self.assertEqual(routes["research"], "research_without_interrupting_owner")

    def test_question_ledger_binds_impact_acceptance_and_resolution_delta(self):
        contract = self.engine.compile_contract(
            ready_source(
                uncertainties=[
                    {
                        "decision_id": "activity-cardinality",
                        "category": "policy_choice",
                        "statement": "程序活动是单值还是集合",
                        "question": "允许多个程序活动并存吗？",
                        "state": "contested",
                        "impact": "high",
                        "acceptance_ids": ["typed-facts"],
                        "consequence": "不同答案会改变 Schema 和 UI",
                        "proposed_default": "使用集合",
                        "decision_owner": "human",
                        "reversible": False,
                    }
                ]
            )
        )

        self.assertEqual(contract["status"], "needs_clarification")
        question = contract["question_gate"]["blocking_questions"][0]
        self.assertEqual(question["decision_id"], "activity-cardinality")
        self.assertEqual(question["acceptance_ids"], ["typed-facts"])

        proposal = self.engine.propose_contract_resolution(
            contract,
            [
                {
                    "decision_id": "activity-cardinality",
                    "answer": "允许多个程序活动并存",
                    "answered_by": "product-owner",
                    "authority": "human",
                    "evidence": "用户在需求闸门中确认",
                }
            ],
        )

        self.assertEqual(proposal["status"], "awaiting_human_attestation")
        self.assertFalse(proposal["resume"]["allowed_now"])
        self.assertEqual(proposal["proposed_contract"]["status"], "ready")
        self.assertNotEqual(
            proposal["delta"]["parent_contract_hash"],
            proposal["delta"]["proposed_contract_hash"],
        )

    def test_low_impact_reversible_assumption_is_visible_but_nonblocking(self):
        contract = self.engine.compile_contract(
            ready_source(
                uncertainties=[
                    {
                        "decision_id": "label-copy",
                        "category": "policy_choice",
                        "statement": "按钮文案使用哪个同义词",
                        "state": "assumed",
                        "impact": "low",
                        "proposed_default": "提交",
                        "decision_owner": "human",
                        "reversible": True,
                    }
                ]
            )
        )

        self.assertEqual(contract["status"], "ready")
        self.assertEqual(
            contract["question_gate"]["non_blocking_routes"][0]["decision_id"],
            "label-copy",
        )

    def test_delegated_high_impact_decision_remains_blocking(self):
        contract = self.engine.compile_contract(
            ready_source(
                uncertainties=[
                    {
                        "decision_id": "delegated-policy",
                        "category": "policy_choice",
                        "statement": "Human owner must choose the policy",
                        "state": "delegated",
                        "impact": "high",
                        "decision_owner": "human",
                    }
                ]
            )
        )

        self.assertEqual(contract["status"], "needs_clarification")
        self.assertEqual(
            contract["question_gate"]["blocking_questions"][0]["decision_id"],
            "delegated-policy",
        )

    def test_empty_contract_resolution_is_rejected(self):
        contract = self.engine.compile_contract(ready_source())

        with self.assertRaisesRegex(ValueError, "must change"):
            self.engine.propose_contract_resolution(contract, [])


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.engine = GovernanceEngine()

    def test_low_risk_work_stays_single_owner_with_deterministic_ci(self):
        contract = self.engine.compile_contract(
            ready_source(
                goal="Correct a documentation typo",
                change_types=["documentation"],
                risk_flags=[],
                constraints=[],
                forbidden_behaviors=[],
            )
        )
        plan = self.engine.route(contract)

        self.assertEqual(plan["risk_level"], "low")
        self.assertEqual([lane["id"] for lane in plan["lanes"]], ["deterministic-ci"])
        self.assertEqual(plan["execution"]["owner_context"], "continuous")
        self.assertEqual(plan["repair_policy"]["max_automatic_rounds"], 1)

    def test_high_risk_work_fans_out_by_failure_mode_not_job_title(self):
        contract = self.engine.compile_contract(ready_source())
        plan = self.engine.route(contract)
        lane_ids = {lane["id"] for lane in plan["lanes"]}

        self.assertTrue(
            {
                "deterministic-ci",
                "contract-domain-semantics",
                "state-trust-boundaries",
                "test-oracle-falsification",
                "security",
                "data-compatibility",
                "e2e-ux",
                "reliability-cost",
                "adversarial-falsification",
            }.issubset(lane_ids)
        )
        for lane in plan["lanes"]:
            self.assertFalse(lane["write_access"])
            self.assertFalse(lane["peer_findings_visible"])
        self.assertIn("high_risk_policy_and_release", plan["human_gates"])
        self.assertEqual(
            [
                "contract-domain-semantics",
                "state-trust-boundaries",
                "test-oracle-falsification",
            ],
            [lane["id"] for lane in plan["lanes"][1:4]],
        )
        self.assertEqual(plan["sequence"][-2:], ["blind_final_verification", "human_handoff"])
        self.assertEqual(plan["checkpoint_policy"]["agent_access_to_control_token"], False)

    def test_context_packets_are_minimal_read_only_and_hash_bound(self):
        contract = self.engine.compile_contract(ready_source())
        plan = self.engine.route(contract)
        contexts = self.engine.context_packets(contract, plan)

        self.assertTrue(contexts)
        self.assertNotIn("deterministic-ci", {item["lane_id"] for item in contexts})
        for item in contexts:
            self.assertEqual(item["permissions"]["repository"], "read")
            self.assertFalse(item["permissions"]["external_writes"])
            self.assertFalse(item["isolation"]["developer_transcript_visible"])
            self.assertEqual(item["contract_hash"], contract["contract_hash"])

        broken = dict(plan)
        broken["contract_hash"] = "different"
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.engine.context_packets(contract, broken)

        weakened = {**plan, "lanes": plan["lanes"][:-1]}
        with self.assertRaisesRegex(ValueError, "plan integrity check failed"):
            self.engine.context_packets(contract, weakened)


class AdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.engine = GovernanceEngine()
        self.contract = self.engine.compile_contract(ready_source())
        self.plan = self.engine.route(self.contract)

    def payload(self, **values):
        result = {
            "contract": self.contract,
            "plan": self.plan,
            "repair_round": 0,
            "deterministic_results": [
                {
                    "name": "unit",
                    "status": "passed",
                    "required": True,
                    "command": "python3 -m unittest",
                    "evidence": "42 tests passed",
                }
            ],
            "findings": [],
        }
        result.update(values)
        return result

    def test_high_signal_reproducible_finding_creates_one_repair_package(self):
        finding = {
            "id": "sec-1",
            "lane": "security",
            "title": "Tenant authorization is bypassed",
            "severity": "high",
            "confidence": 94,
            "location": "app/settings.py:42",
            "evidence": "The query filters only by record id",
            "reproduction": "Request tenant B record id while authenticated as tenant A",
            "introduced_by_change": True,
        }
        duplicate = dict(finding, id="req-2", lane="contract-domain-semantics", confidence=85)
        verdict = self.engine.adjudicate(self.payload(findings=[finding, duplicate]))

        self.assertEqual(verdict["decision"], "repair_once")
        self.assertEqual(len(verdict["semantic_blockers"]), 1)
        self.assertEqual(verdict["metrics"]["deduplicated_findings"], 1)
        self.assertEqual(verdict["repair_package"]["owner"], "original_owner_context")

    def test_low_confidence_preexisting_and_unreproduced_findings_do_not_block(self):
        findings = [
            {
                "lane": "code-architecture",
                "title": "Maybe complex",
                "severity": "high",
                "confidence": 60,
                "evidence": "subjective",
                "reproduction": "none",
                "introduced_by_change": True,
            },
            {
                "lane": "security",
                "title": "Old issue",
                "severity": "high",
                "confidence": 99,
                "evidence": "exists on main",
                "reproduction": "reproduces on base and head",
                "introduced_by_change": False,
            },
            {
                "lane": "security",
                "title": "No proof",
                "severity": "critical",
                "confidence": 99,
                "evidence": "dangerous-looking call",
                "reproduction": "",
                "introduced_by_change": True,
            },
        ]
        verdict = self.engine.adjudicate(self.payload(findings=findings))

        self.assertEqual(verdict["decision"], "ready_for_final_verification")
        self.assertEqual(len(verdict["rejected_findings"]), 3)

    def test_required_ci_failure_blocks_and_second_round_escalates(self):
        failed = [
            {
                "name": "unit",
                "status": "failed",
                "required": True,
                "command": "python3 -m unittest",
                "evidence": "one assertion failed",
            }
        ]
        first = self.engine.adjudicate(
            self.payload(deterministic_results=failed, repair_round=0)
        )
        second = self.engine.adjudicate(
            self.payload(deterministic_results=failed, repair_round=1)
        )

        self.assertEqual(first["decision"], "repair_once")
        self.assertEqual(second["decision"], "human_decision")
        self.assertEqual(second["repair_package"]["automatic_rounds_remaining"], 0)

    def test_missing_required_deterministic_evidence_blocks(self):
        verdict = self.engine.adjudicate(
            self.payload(deterministic_results=[], repair_round=0)
        )

        self.assertEqual(verdict["decision"], "repair_once")
        self.assertEqual(verdict["deterministic_blockers"][0]["status"], "missing")

    def test_unselected_lane_and_unproven_change_scope_cannot_block(self):
        findings = [
            {
                "lane": "invented-reviewer",
                "title": "Arbitrary blocker",
                "severity": "critical",
                "confidence": 100,
                "evidence": "unsupported lane",
                "reproduction": "run it",
                "introduced_by_change": True,
            },
            {
                "lane": "security",
                "title": "Scope is unknown",
                "severity": "high",
                "confidence": 100,
                "evidence": "could exist",
                "reproduction": "run it",
                "introduced_by_change": "unknown",
            },
        ]
        verdict = self.engine.adjudicate(self.payload(findings=findings))

        self.assertEqual(verdict["decision"], "ready_for_final_verification")
        self.assertEqual(
            {item["rejected_reason"] for item in verdict["rejected_findings"]},
            {"lane_not_enabled_by_plan", "introduced_by_change_unproven"},
        )

    def test_disputed_high_risk_fact_goes_directly_to_human(self):
        finding = {
            "lane": "data-compatibility",
            "title": "Migration reversibility is disputed",
            "severity": "high",
            "confidence": 90,
            "evidence": "Down migration conflicts with retained rows",
            "reproduction": "Run up then down against the fixture",
            "introduced_by_change": True,
            "disputed": True,
        }
        verdict = self.engine.adjudicate(self.payload(findings=[finding]))
        self.assertEqual(verdict["decision"], "human_decision")

    def test_cross_lane_root_cause_is_merged_into_one_repair_item(self):
        common = {
            "severity": "high",
            "confidence": 0.95,
            "introduced_by_change": True,
            "root_cause_key": "typed-correction-trust-boundary",
            "violated_invariant": "Corrections retain their declared type",
            "counterexample": "Submit integer 0 through the Boolean-only UI",
            "artifact_refs": ["tests/correction-e2e.json"],
            "reproduction": {
                "preconditions": ["Open correction form"],
                "steps": ["Submit integer 0"],
                "expected": "Integer 0 is stored",
                "actual": "The UI cannot submit it",
            },
            "evidence": "Browser trace and API response disagree",
        }
        findings = [
            dict(common, id="state-1", lane="state-trust-boundaries", title="Typed correction is lost"),
            dict(common, id="oracle-1", lane="test-oracle-falsification", title="E2E misses typed correction"),
        ]
        verdict = self.engine.adjudicate(
            self.payload(
                findings=findings,
                inspector_telemetry=[
                    {
                        "lane": "state-trust-boundaries",
                        "duration_ms": 1200,
                        "input_tokens": 300,
                        "output_tokens": 90,
                    }
                ],
            )
        )

        self.assertEqual(verdict["decision"], "repair_once")
        self.assertEqual(len(verdict["repair_package"]["root_causes"]), 1)
        self.assertEqual(
            set(verdict["repair_package"]["root_causes"][0]["contributing_lanes"]),
            {"state-trust-boundaries", "test-oracle-falsification"},
        )
        state_metrics = verdict["metrics"]["per_inspector"]["state-trust-boundaries"]
        self.assertEqual(state_metrics["duration_ms"], 1200)
        self.assertEqual(state_metrics["input_tokens"], 300)


class IntegrationAndScaffoldTests(unittest.TestCase):
    def test_blueprint_keeps_governance_stateless(self):
        blueprint = integration_blueprint()
        governance = next(
            item for item in blueprint["components"] if item["name"] == "AI Delivery Governance"
        )
        self.assertIn("tasks", governance["must_not_own"])
        self.assertEqual(blueprint["default_path"]["development_control_plane"], "Kandev")

    def test_scaffold_writes_a_versioned_bundle_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            result = scaffold_project(repo, ready_source())

            self.assertEqual(result["contract_status"], "ready")
            self.assertIn("delivery-handoff.json", result["next"])
            self.assertTrue((repo / ".ai-delivery" / "contract.json").is_file())
            self.assertTrue((repo / ".ai-delivery" / "verification-plan.json").is_file())
            self.assertTrue((repo / ".ai-delivery" / "delivery-handoff.json").is_file())
            registry = json.loads(
                (repo / ".ai-delivery" / "bad-case-registry.json").read_text(
                    encoding="utf-8"
                )
            )
            protocol = json.loads(
                (repo / ".ai-delivery" / "runtime-protocol.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(protocol["schema_version"], "2.3")
            self.assertEqual(
                protocol["bad_case_registry_hash"], registry["registry_hash"]
            )
            self.assertTrue(
                protocol["contract_resolution"]["human_delta_attestation_required"]
            )
            content = (repo / ".ai-delivery" / "CONSTITUTION.md").read_text(
                encoding="utf-8"
            )
            with self.assertRaisesRegex(FileExistsError, "no files were changed"):
                scaffold_project(repo, ready_source(goal="Different goal"))
            self.assertEqual(
                (repo / ".ai-delivery" / "CONSTITUTION.md").read_text(encoding="utf-8"),
                content,
            )

    def test_delivery_handoff_preserves_owner_context_and_inspector_boundaries(self):
        engine = GovernanceEngine()
        contract = engine.compile_contract(ready_source())
        plan = engine.route(contract)
        handoff = engine.delivery_handoff(contract, plan)

        self.assertEqual(handoff["status"], "awaiting_intent_attestation")
        self.assertFalse(handoff["owner_task"]["creation_allowed"])
        self.assertEqual(handoff["owner_task"]["session"], "continuous_until_handoff_or_single_repair")
        self.assertFalse(handoff["executor"]["api_key_allowed"])
        self.assertTrue(handoff["inspector_tasks"])
        self.assertTrue(
            all(
                item["session"] == "new_task_and_fresh_session"
                for item in handoff["inspector_tasks"]
            )
        )
        self.assertIn("push", handoff["kandev"]["workflow_warning"])


if __name__ == "__main__":
    unittest.main()
