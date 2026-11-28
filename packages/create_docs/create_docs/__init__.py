"""Doc and slides harness.

The provider writes the file (Anthropic skills pptx, docx, xlsx, pdf, routed
by the shared Create adapter). This package formats context, judges the file,
keeps versions, and hands the PDF sha256 to sign_request. It does not render.
"""
