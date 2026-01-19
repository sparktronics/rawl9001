# Testing Rules

## Test Strategy

### Happy Path
- Test the expected flow with valid inputs
- Verify correct return values and side effects
- Use realistic fixtures that match actual API responses

### Error Handling
- Test with missing/invalid inputs
- Verify correct exceptions are raised
- Test boundary conditions (empty lists, None values)

### Keep Tests Focused
- One assertion concept per test
- Test name describes what's being verified
- Mock external dependencies (APIs, storage)

---

## Patterns from Existing Tests

### Fixtures for Reusable Test Data

```python
@pytest.fixture
def ado_client():
    """Create client instance for testing."""
    return AzureDevOpsClient(
        org="test-org",
        project="test-project", 
        repo="test-repo",
        pat="fake-pat-token",
    )

@pytest.fixture
def sample_pr():
    """Sample PR metadata matching real API response."""
    return {
        "pullRequestId": 12345,
        "title": "Add new feature",
        "createdBy": {"displayName": "John Doe"},
        "sourceRefName": "refs/heads/feature/new-feature",
        "targetRefName": "refs/heads/main",
    }
```

### Mocking External APIs

```python
def test_request_get_success(self, ado_client, mocker):
    """GET request returns JSON response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": 1, "name": "test"}
    
    mocker.patch("main.requests.request", return_value=mock_response)
    
    result = ado_client._get("/test/endpoint")
    
    assert result == {"id": 1, "name": "test"}
```

### Testing Pure Logic (No Mocks Needed)

```python
def test_get_max_severity_blocking(self):
    """Returns 'blocking' when blocking severity found."""
    review = "**Severity:** blocking"
    assert get_max_severity(review) == "blocking"

def test_get_max_severity_empty_review(self):
    """Returns 'info' for empty review."""
    assert get_max_severity("") == "info"
```

### Mocking Cloud Storage

```python
def test_save_to_storage_success(self, mocker):
    """save_to_storage uploads review to GCS bucket."""
    mock_blob = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket
    
    mocker.patch("main.storage.Client", return_value=mock_client)
    
    result = save_to_storage("test-bucket", 12345, "review content")
    
    mock_blob.upload_from_string.assert_called_once()
    assert result.startswith("gs://test-bucket/")
```

---

## Running Tests

```bash
# Run all tests
python3 -m pytest test_main.py -v

# Run specific test class
pytest test_main.py::TestGetMaxSeverity -v

# Run with coverage
pytest test_main.py --cov=main --cov-report=term
```

---

## Local Function Testing

```bash
# Start local server
python3 -m functions_framework --target=review_pr --debug --port=8080

# Test with curl
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -H "X-API-Key: test-key" \
  -d '{"pr_id": 12345}'
```

---

## Pre-Deployment Checks

1. `python3 -m py_compile main.py` — syntax OK
2. `pytest test_main.py -v` — all tests pass
3. Local function starts without import errors
4. No secrets in committed code
