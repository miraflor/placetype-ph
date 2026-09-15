# PlaceType PH 0.1.1

This patch is the first release driven by **live PSA workbooks**, not synthetic importer fixtures.

## Live-source corrections

- Added a PSIC Revision 5 matrix parser for the official `Section / Division / Group / Class / Sub-Class / Description` layout. A row may define several hierarchy nodes; all are now retained.
- Added a PSCC 2022 parser for the official `Heading / 2022 PSCC / DESCRIPTION` layout and `Chapter n` prose rows.
- PSCC now materializes omitted 2/4/6/8-digit prefix ancestors needed for hierarchical backoff. These nodes are explicitly marked as implicit source-structure nodes rather than presented as workbook titles.
- Repairs the workbook's numeric `98.1` heading to `9810` because it is stored as a number with Excel format `0.00` in a column that unambiguously represents 4-digit headings.
- Intermediate-level gaps are diagnostic warnings by default. The current official PSIC workbook contains three subclass rows whose 4-digit parent is absent, so rejecting every gap makes the official source unusable.
- The contested PSIC group count is no longer a hard structure gate: PSA's current web summary says 260 groups, while the current official July 2026 workbook contains 261 distinct group rows. Sections, divisions, classes and subclasses remain externally checked.

## Live results used for this patch

The current PSIC workbook parses to 22 sections, 88 divisions, 261 groups, 493 classes and 1,338 subclasses. It has no orphan/wrong-parent errors and three source-level gaps under group 523.

The current PSCC 2022 workbook parses to a complete five-level prefix tree with 98 chapters (including the reserved chapter), 1,245 headings, 5,702 HS subheadings, 11,522 AHTN subheadings and 16,049 11-digit commodities, with no structural errors or level gaps.
