# Recipe-site helper commands. Run `make help` for the list.

BASE ?= http://localhost:8000

.PHONY: help install dev-install build serve validate clean

help:
	@echo "make install      install runtime deps (Jinja2)"
	@echo "make dev-install  install runtime + dev deps (adds jsonschema)"
	@echo "make validate     validate plans/*.json against the schema"
	@echo "make build        render the site into ./site"
	@echo "make serve        build, then serve ./site at http://localhost:8000"
	@echo "make clean        remove ./site"

install:
	pip install -r requirements.txt

dev-install:
	pip install -r requirements.txt -r requirements-dev.txt

validate:
	python validate.py

build:
	PAGES_BASE_URL="$(BASE)" python render.py

serve: build
	@echo "Serving on http://localhost:8000  (Ctrl-C to stop)"
	cd site && python -m http.server 8000

clean:
	rm -rf site __pycache__
