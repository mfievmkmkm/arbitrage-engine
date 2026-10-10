from app.failure_matrix_runner import evaluate
from app.failure_matrix import cases
def test_matrix_runner_requires_every_case():
 good={c.name:c.expected for c in cases()};assert evaluate(good).passed
 good.pop("stale_books");assert not evaluate(good).passed
