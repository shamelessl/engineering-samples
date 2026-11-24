"""
Самостоятельный воспроизводимый пример (запуск: `python3 03_rag_retrieval_vs_citation_eval.py`):
Детерминированная проверка фиксированных фикстур RAG (разделение промаха поиска `Recall@k` и нарушений
цитирования/границ/отказа на записанных ответах модели без вызова внешней LLM).

Классы исходов на контрольных фикстурах:
1. GROUNDED_OK — эталонные чанки присутствуют в top-k, процитированы точные ID, присутствует ожидаемое
   утверждение без отрицающих инверсий и без запрещённых граничных формулировок.
2. RETRIEVAL_MISS — эталонный чанк не попал в top-k.
3. HALLUCINATION_OR_BOUNDARY_ERROR — чанк найден, но ссылка неверна, перевёрнуто утверждение («неверно, что...»),
   нарушено строгое неравенство (`> 15` против `ровно 15`) или к отказу дописана выдуманная цифра.
4. CORRECT_ABSTAIN — ответа нет в корпусе, и модель вернула чистый отказ по шаблону без ссылок и без цифр.
"""
from __future__ import annotations

import re
import unittest
from dataclasses import dataclass


@dataclass(frozen=True)
class DocChunk:
    chunk_id: str
    text: str
    forbidden_phrases: tuple[str, ...] = ()
    reviewed_answers: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    question: str
    expected_chunk_ids: tuple[str, ...]
    retrieved_top_k: tuple[str, ...]
    recorded_model_answer: str
    must_contain_fact: str | None


CORPUS: dict[str, DocChunk] = {
    "REG-101": DocChunk(
        chunk_id="REG-101",
        text="Пункт 4.2. Возврат предоплаты оформляется по письменному заявлению клиента в течение 10 рабочих дней.",
        forbidden_phrases=("заявление не требуется", "без письменного заявления"),
        reviewed_answers=("Возврат оформляется по письменному заявлению в течение 10 рабочих дней.",),
    ),
    "REG-204": DocChunk(
        chunk_id="REG-204",
        text="Пункт 7.1. Перенос слота допускается только при задержке строго более чем на 15 минут. При задержке до 15 минут включительно слот сохраняется.",
        forbidden_phrases=("при задержке на 15 минут слот переносится", "ровно на 15 минут перенос"),
        reviewed_answers=("При задержке до 15 минут включительно слот сохраняется.",),
    ),
    "REG-309": DocChunk(
        chunk_id="REG-309",
        text="Пункт 9.4. Выгрузка закрывающих актов формируется в личном кабинете 5-го рабочего дня месяца.",
        reviewed_answers=("Выгрузка закрывающих актов формируется в личном кабинете 5-го рабочего дня месяца.",),
    ),
}

CITE_RE = re.compile(r"\[(REG-\d+)\]")
DIGIT_RE = re.compile(r"\d")
NEGATION_PREFIXES = ("неверно, что ", "ложно, что ", "не правда, что ", "не ")
ALLOWED_ABSTAIN_EXACT = "нет данных в регламенте по этому вопросу."


def classify_rag_case(case: EvalCase, corpus: dict[str, DocChunk]) -> str:
    cited_ids = CITE_RE.findall(case.recorded_model_answer)
    ans_norm = " ".join(case.recorded_model_answer.strip().lower().split())

    # 1. Вопрос вне корпуса: принимаем только чистый отказ без ссылок и без дописанных чисел/фактов
    if not case.expected_chunk_ids:
        if not cited_ids and not DIGIT_RE.search(ans_norm) and ans_norm == ALLOWED_ABSTAIN_EXACT:
            return "CORRECT_ABSTAIN"
        return "HALLUCINATION_OR_BOUNDARY_ERROR"

    # 2. Проверка полноты поиска по эталонным ID
    if not set(case.expected_chunk_ids).issubset(set(case.retrieved_top_k)):
        return "RETRIEVAL_MISS"

    # 3. Проверка точного совпадения множества цитат
    if set(cited_ids) != set(case.expected_chunk_ids):
        return "HALLUCINATION_OR_BOUNDARY_ERROR"

    for cid in cited_ids:
        chunk = corpus.get(cid)
        if chunk is None:
            return "HALLUCINATION_OR_BOUNDARY_ERROR"
        for bad_phrase in chunk.forbidden_phrases:
            if bad_phrase in ans_norm:
                return "HALLUCINATION_OR_BOUNDARY_ERROR"

    if case.must_contain_fact:
        fact = case.must_contain_fact.lower()
        if fact not in ans_norm:
            return "HALLUCINATION_OR_BOUNDARY_ERROR"
        for neg in NEGATION_PREFIXES:
            if f"{neg}{fact}" in ans_norm:
                return "HALLUCINATION_OR_BOUNDARY_ERROR"

    # Успех требует совпадения всего ответа с проверенной формулировкой.
    # Незнакомый пересказ остаётся на ручном разборе даже при совпадении срока и ссылки.
    without_citations = CITE_RE.sub("", case.recorded_model_answer)
    normalized = " ".join(without_citations.casefold().split()).replace(" .", ".")
    if len(case.expected_chunk_ids) == 1:
        approved = corpus[case.expected_chunk_ids[0]].reviewed_answers
        if normalized in {text.casefold() for text in approved}:
            return "GROUNDED_OK"
    return "NEEDS_REVIEW"


def evaluate_suite(cases: list[EvalCase]) -> dict[str, float | dict[str, int]]:
    counts = {
        "GROUNDED_OK": 0,
        "RETRIEVAL_MISS": 0,
        "HALLUCINATION_OR_BOUNDARY_ERROR": 0,
        "CORRECT_ABSTAIN": 0,
        "NEEDS_REVIEW": 0,
    }
    answerable = [c for c in cases if c.expected_chunk_ids]
    recall_sum = 0.0
    for c in cases:
        verdict = classify_rag_case(c, CORPUS)
        counts[verdict] += 1
        if c.expected_chunk_ids:
            hits = len(set(c.expected_chunk_ids) & set(c.retrieved_top_k))
            recall_sum += hits / len(c.expected_chunk_ids)

    norm_recall_at_k = recall_sum / len(answerable) if answerable else 0.0
    return {"counts": counts, "recall_at_k": round(norm_recall_at_k, 3)}


class RagDiagnosticHarnessTests(unittest.TestCase):
    def test_correct_deadline_does_not_validate_the_rest_of_the_answer(self):
        responses = {
            "Через 10 рабочих дней. Письменное заявление не требуется [REG-101].":
                "HALLUCINATION_OR_BOUNDARY_ERROR",
            "Возврат оформляется по письменному заявлению в течение 10 рабочих дней. Комиссия 30% [REG-101].":
                "NEEDS_REVIEW",
            "Срок возврата при наличии заявления: 10 рабочих дней [REG-101].":
                "NEEDS_REVIEW",
            "Возврат оформляется по письменному заявлению в течение 10 рабочих дней [REG-101].":
                "GROUNDED_OK",
        }
        for answer, expected in responses.items():
            with self.subTest(answer=answer):
                case = EvalCase("refund", "Как вернуть предоплату?", ("REG-101",), ("REG-101",),
                                answer, "10 рабочих дней")
                self.assertEqual(classify_rag_case(case, CORPUS), expected)

    def test_separates_retrieval_miss_boundary_negation_and_tainted_abstain(self) -> None:
        suite = [
            EvalCase(
                case_id="ok_refund",
                question="В какой срок оформляется возврат предоплаты?",
                expected_chunk_ids=("REG-101",),
                retrieved_top_k=("REG-101", "REG-309"),
                recorded_model_answer="Возврат оформляется по письменному заявлению в течение 10 рабочих дней [REG-101].",
                must_contain_fact="10 рабочих дней",
            ),
            EvalCase(
                case_id="boundary_15min_strict_violation",
                question="Переносится ли слот при задержке ровно на 15 минут?",
                expected_chunk_ids=("REG-204",),
                retrieved_top_k=("REG-204", "REG-101"),
                recorded_model_answer="Да, при задержке на 15 минут слот переносится [REG-204].",
                must_contain_fact="слот сохраняется",
            ),
            EvalCase(
                case_id="negated_fact_caught",
                question="Переносится ли слот при задержке ровно на 15 минут?",
                expected_chunk_ids=("REG-204",),
                retrieved_top_k=("REG-204", "REG-101"),
                recorded_model_answer="Неверно, что слот сохраняется [REG-204].",
                must_contain_fact="слот сохраняется",
            ),
            EvalCase(
                case_id="boundary_15min_valid",
                question="Переносится ли слот при задержке ровно на 15 минут?",
                expected_chunk_ids=("REG-204",),
                retrieved_top_k=("REG-204", "REG-309"),
                recorded_model_answer="При задержке до 15 минут включительно слот сохраняется [REG-204].",
                must_contain_fact="слот сохраняется",
            ),
            EvalCase(
                case_id="retrieval_failed",
                question="Какого числа формируются закрывающие акты?",
                expected_chunk_ids=("REG-309",),
                retrieved_top_k=("REG-101", "REG-204"),
                recorded_model_answer="Нет данных в регламенте по этому вопросу.",
                must_contain_fact="5-го рабочего дня",
            ),
            EvalCase(
                case_id="clean_abstain",
                question="Какой штраф за утерю пропуска на парковку?",
                expected_chunk_ids=(),
                retrieved_top_k=("REG-101", "REG-204"),
                recorded_model_answer="Нет данных в регламенте по этому вопросу.",
                must_contain_fact=None,
            ),
            EvalCase(
                case_id="tainted_abstain_with_invented_penalty_caught",
                question="Какой штраф за утерю пропуска на парковку?",
                expected_chunk_ids=(),
                retrieved_top_k=("REG-101", "REG-204"),
                recorded_model_answer="Нет данных в регламенте по этому вопросу. Но штраф 900 рублей.",
                must_contain_fact=None,
            ),
        ]

        report = evaluate_suite(suite)
        self.assertEqual(
            report["counts"],
            {
                "GROUNDED_OK": 2,
                "RETRIEVAL_MISS": 1,
                "HALLUCINATION_OR_BOUNDARY_ERROR": 3,
                "CORRECT_ABSTAIN": 1,
                "NEEDS_REVIEW": 0,
            },
        )
        self.assertEqual(report["recall_at_k"], 0.8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
