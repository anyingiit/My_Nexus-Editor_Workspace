.PHONY: validate demo test compile sandbox-check release clean

SOURCE_DATE_EPOCH ?= 1786060800
RELEASE_OUTPUT ?= ../agents-v3-release

validate:
	PYTHONPATH=src python3 -m agents_v3 validate

demo:
	PYTHONPATH=src python3 -m agents_v3 demo --output work/demo-run

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

compile:
	python3 -m compileall -q src tests sandbox/run_with_limits.py

sandbox-check:
	sh -n sandbox/run-untrusted.sh

release:
	SOURCE_DATE_EPOCH=$(SOURCE_DATE_EPOCH) python3 tools/build_release.py --output $(RELEASE_OUTPUT)

clean:
	@echo "Refusing implicit destructive cleanup; remove generated work/dist paths explicitly."
