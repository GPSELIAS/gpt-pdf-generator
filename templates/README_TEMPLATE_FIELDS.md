## Template-Felder (Jinja2)

### Global
- **document_title**: Optionaler HTML-Titel (Fallback: `"PDF"`)
- **title**: Cover-Claim (wird auf dem Cover als großer Text unten rechts gesetzt)
- **sections**: Liste von Sections; jede Section braucht mindestens `layout`

### Section (Layout `type_a`) – `partials/page_type_a.html`
- **section.layout**: `"type_a"`
- **section.title**
- **section.intro**
- **section.image_1**
- **section.image_2**
- **section.item_1_title**
- **section.item_1_text**
- **section.item_2_title**
- **section.item_2_text**
- **section.item_3_title**
- **section.item_3_text**
- **section.item_4_title**
- **section.item_4_text**

### Section (Layout `type_b`) – `partials/page_type_b.html`
- **section.layout**: `"type_b"`
- **section.title**
- **section.intro**
- **section.factor_1_title**
- **section.factor_1_text**
- **section.factor_2_title**
- **section.factor_2_text**
- **section.factor_3_title**
- **section.factor_3_text**
- **section.factor_4_title**
- **section.factor_4_text**
- **section.factor_5_title**
- **section.factor_5_text**
- **section.factor_6_title**
- **section.factor_6_text**

### Abschlussseite – `partials/closing.html`
- **end_title**
- **end_text**
- **address**
- **telephone**
- **website**

### Bilder / Assets

- **Statische Layout-Bilder** liegen unter `templates/assets/` und werden in den Templates als `assets/...` referenziert.
- **Dynamische Bilder** (z.B. `section.image_1`) sollten als Pfad/URL vorliegen, die WeasyPrint erreichen kann (typisch: absoluter Pfad oder relativ zur `base_url` beim Rendern).

