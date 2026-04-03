# 1. Initialize config
python -m spacy init config config.cfg --lang en --pipeline ner --optimize efficiency

# 2. Train model
python -m spacy train config.cfg --output ./output --paths.train ./train.spacy --paths.dev ./train.spacy
