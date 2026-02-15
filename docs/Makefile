SPHINXOPTS    ?=
SPHINXBUILD   ?= ~/owuinc/.venv/bin/sphinx-build
SOURCEDIR     = source
BUILDDIR      = build

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS)

clean:
	rm -rf $(BUILDDIR)/*.doctrees $(BUILDDIR)/*/*.doctrees

.PHONY: help Makefile clean

%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS)
