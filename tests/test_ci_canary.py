def test_deliberately_broken() -> None:
    # Deliberate failure to prove CI catches a broken test (issue #4).
    # This commit is reverted immediately after CI is seen to fail.
    assert 1 + 1 == 3
