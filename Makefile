
.PHONY: refresh

foo:


refresh: data/raw/*


data/raw:
	mkdir -p data/raw

data/raw/unit_number: data/raw
	python getwarddata.py data/raw
