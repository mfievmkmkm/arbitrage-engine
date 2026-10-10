from app.dex_failure_matrix import CASES,complete
def test_dex_matrix_requires_every_case():
 assert complete({x:True for x in CASES})[0]
 assert not complete({})[0]
