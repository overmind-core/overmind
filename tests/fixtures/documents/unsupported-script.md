# Unsupported script regression fixture

`unsupported-script.pdf` retains one Spanish screenshot from the public
[CSIRT image dataset](https://github.com/farandal/csirt-img-ml-model/blob/8a683a7e3a23c05a742f4bb3a24723c5587242ba/dataset/csirt_extracted/13BCS20-00055-01/13BCS20-00055-01_p12_01.jpeg).
It depicts an example reported as phishing by Chilean government CSIRT; domains
and instructions in the screenshot are evidence, not links to follow.

- Source bulletin: `13BCS20-00055-01.pdf`, page 12, alert `8FPH20-00222-01`.
- Original JPEG SHA-256: `c04538116aad17747625207a581bc11f22b20a0191ea385d1de38a1fc156f9b5`.
- Retained PDF page: 58 of the staging `csirt-screens-01.pdf` source.

Tesseract 5.3.0 reports Fraktur for the rendered screenshot. English first-pass
recognition provides enough text evidence to select Spanish. The parser must
retain the unsupported script guess without rejecting the whole document.
