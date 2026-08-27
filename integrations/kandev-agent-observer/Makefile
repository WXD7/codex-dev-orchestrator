GO_BIN ?= go
KANDEV_BACKEND ?=
OUTPUT ?= dist/ai-delivery-agent-observer-0.4.3-darwin-arm64.tar.gz
BASE_PACKAGE ?= dist/ai-delivery-agent-observer-0.4.1-darwin-arm64.tar.gz

.PHONY: test package package-ui-hotfix

test:
	@test -n "$(KANDEV_BACKEND)" || (echo "KANDEV_BACKEND is required" && exit 2)
	python3 tools/package_plugin.py --test-only --go-bin "$(GO_BIN)" --kandev-backend "$(KANDEV_BACKEND)"

package:
	@test -n "$(KANDEV_BACKEND)" || (echo "KANDEV_BACKEND is required" && exit 2)
	python3 tools/package_plugin.py --go-bin "$(GO_BIN)" --kandev-backend "$(KANDEV_BACKEND)" --output "$(OUTPUT)"

package-ui-hotfix:
	python3 tools/package_plugin.py --reuse-binary-package "$(BASE_PACKAGE)" --output "$(OUTPUT)"
