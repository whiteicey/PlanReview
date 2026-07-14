from app.storage.hashing import sha256_bytes, sha256_text


def test_sha256_bytes_known_vector():
    assert sha256_bytes(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_sha256_text_utf8():
    assert sha256_text("abc") == sha256_bytes("abc".encode("utf-8"))
