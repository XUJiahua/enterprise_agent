.PHONY: build check test deps experiment
build:
	mkdir -p bin
	go build -o bin/server ./cmd/server
	go build -o bin/appctl ./cmd/appctl
check: build
	bin/appctl verify
test: build
	go test -race ./...
	go vet ./...
	mkdir -p reports
	python3 tests/verifier.py > reports/verifier.json
deps:
	npm ci --prefix apps/zenstack
	npm run generate --prefix apps/zenstack
	npm run typecheck --prefix apps/zenstack
experiment: build deps
	python3 scripts/experiment.py --route baseline
	python3 scripts/experiment.py --route zenstack
	python3 scripts/experiment.py --route guarded
web-deps:
	npm ci --prefix apps/web
	npm run build --prefix apps/web
	apps/web/node_modules/.bin/playwright install chromium
browser:
	python3 scripts/browser.py --route $(ROUTE)
benchmark-smoke:
	python3 benchmark/smoke.py
