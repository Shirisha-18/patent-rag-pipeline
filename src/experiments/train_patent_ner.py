import spacy
from spacy.tokens import DocBin
from spacy.training.example import Example
import json
from pathlib import Path

# -----------------------------
# Labels
# -----------------------------
LABELS = [
    "PATENT_NUMBER",
    "SERIAL_NUMBER",
    "APPLICATION_DATE",
    "PATENT_DATE",
    "INVENTOR",
    "ASSIGNEE",
    "PATENT_TITLE",
]

# -----------------------------
# Create blank English model
# -----------------------------
nlp = spacy.blank("en")
if "ner" not in nlp.pipe_names:
    ner = nlp.add_pipe("ner")
else:
    ner = nlp.get_pipe("ner")

for label in LABELS:
    ner.add_label(label)

# -----------------------------
# Load silver labels
# -----------------------------
with open("../output/silver_labels.json", "r", encoding="utf-8") as f:
    silver_data = json.load(f)


# -----------------------------
# Helper: create entities with overlap handling
# -----------------------------
def create_entities(text, item):
    entities = []
    seen_spans = set()  # character positions already used

    def add_entity(start, end, label):
        if start < end:
            span_range = set(range(start, end))
            if span_range & seen_spans:
                # overlapping entity → skip
                return
            entities.append((start, end, label))
            seen_spans.update(span_range)

    # Fixed fields
    for label, key in [
        ("PATENT_NUMBER", "patent_number"),
        ("SERIAL_NUMBER", "serial_number"),
        ("APPLICATION_DATE", "application_date"),
        ("PATENT_DATE", "patent_date"),
        ("PATENT_TITLE", "title"),
    ]:
        if item[key]:
            start = text.find(item[key])
            if start != -1:
                add_entity(start, start + len(item[key]), label)

    # Inventors
    for inv in item.get("inventors", []):
        start = text.find(inv)
        if start != -1:
            add_entity(start, start + len(inv), "INVENTOR")

    # Assignees
    for ass in item.get("assignees", []):
        start = text.find(ass)
        if start != -1:
            add_entity(start, start + len(ass), "ASSIGNEE")

    return entities


# -----------------------------
# Create DocBin for training
# -----------------------------
db = DocBin()
for item in silver_data:
    text = item["header"]
    entities = create_entities(text, item)
    if entities:
        doc = nlp.make_doc(text)
        example = Example.from_dict(doc, {"entities": entities})
        db.add(example.reference)

# Save training data
train_path = Path("./train.spacy")
train_path.write_bytes(db.to_bytes())
print(f"Training data saved to {train_path}")

# -----------------------------
# Train NER model (SpaCy v3+)
# -----------------------------
from spacy.util import minibatch, compounding

optimizer = nlp.begin_training()
examples = []

# Convert all DocBin docs to Example objects
for doc in db.get_docs(nlp.vocab):
    # Empty reference doc
    example = Example.from_dict(
        doc,
        {"entities": [(ent.start_char, ent.end_char, ent.label_) for ent in doc.ents]},
    )
    examples.append(example)

for epoch in range(30):
    losses = {}
    batches = minibatch(examples, size=compounding(4.0, 32.0, 1.5))
    for batch in batches:
        nlp.update(batch, sgd=optimizer, losses=losses)
    print(f"Epoch {epoch + 1}/30 — Losses: {losses}")

# Save trained model
model_path = Path("./patent_ner")
nlp.to_disk(model_path)
print(f"Custom SpaCy NER model saved at {model_path}")
