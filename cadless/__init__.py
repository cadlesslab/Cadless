"""Cadless — natural-language-driven CAD PoC.

Turns a plain-language part description into an authoritative B-Rep solid by
generating build123d code with AWS Bedrock, validating it, executing it in an
isolated worker, and exporting STEP (engineering) + glTF (display).

 for the locked architecture decisions.
"""

__version__ = "1.0.0"
