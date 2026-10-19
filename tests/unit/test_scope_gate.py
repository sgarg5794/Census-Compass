from census_compass.chat.scope_gate import classify_dataset_question


def test_allows_supported_dataset_questions():
    assert classify_dataset_question("How many people live in California?") == "dataset"
    assert classify_dataset_question("Compare median household income in Texas and Florida") == "dataset"


def test_declines_unrelated_and_code_requests_before_agent():
    assert classify_dataset_question("What is the weather in San Francisco?") == "out_of_scope"
    assert classify_dataset_question("Write Python code to query Census population") == "out_of_scope"
    assert classify_dataset_question("How many people live in California and what is the weather?") == "out_of_scope"


def test_declines_general_census_questions_until_document_retrieval_exists():
    assert classify_dataset_question("How does the Census Bureau conduct the ACS?") == "unsupported_general_census"


def test_unclear_inputs_fail_closed():
    assert classify_dataset_question("Tell me about San Francisco") == "clarify"
