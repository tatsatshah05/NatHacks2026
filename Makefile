.PHONY: test web-typecheck web-build

test: web-typecheck

web-typecheck:
	cd web && npm run typecheck

web-build:
	cd web && npm run build
