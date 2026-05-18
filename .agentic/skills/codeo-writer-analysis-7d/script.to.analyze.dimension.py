# maintainer: starlight.ai
# author: starlight.ai
# date: May 18, 2026
# version: v0.0.3
# purpose: CLI executable for the purposes of running a starlight codeo dimension analysis on a set of writing
# functional: BROKEN
# changelog:
#  v0.0.2 => removed CLI interface and references to third-party LLM APIs to prepare for publication
#  v0.0.3 => added header for May 18th, 2026 public publication
# design methodology:
#  - CLI interfaces allow software engineering auto-coders to run processes directly in sandboxed environments
#  - per-dimension calculation allows smaller context and parallel execution
#  - use of reference sources aids interpretation in a specific context in which the writing sample was created

# NOTE: this document only exists as pseudo-code to provide the PROMPT and template string-replace code.
# NOTE: this document does not describe version control methods, nor atomic storage of results in CloudNode
# NOTE: this document does not describe transparency and traceability of foundational data inside an organization

PROMPT_DIMENSIONS_ANALYSIS = """
Role: You are a Computational Linguist and Linguistic Style Analyst specializing in accurate detection of human written {__DIMENSION}. Your task is to conduct a precise evaluation of the AUTHOR TEXT using the provided {__DIMENSION} framework to critically determine its linguistic definition. The AUTHOR TEXT was written to express the ideas drawn from the provided REFERENCE SOURCES and this will help to contrast the precise linguistic qualities the author of the AUTHOR TEXT is using to express their individual thoughts. This uses a modular structure to force the categorization of all data, ensuring no detail is missed. The FRAMEWORK ensures the specific repeatable critical terminology of your analysis.

# INPUTS
1. AUTHOR TEXT: a user-authored commentary.
2. REFERENCE SOURCES: a set of primary sources directly used by the author
3. FRAMEWORK: the specific {__N_POINTS} descriptors of {__DIMENSION} including definitions and examples.

# TASK
1. ANALYZE FOR CONTRAST: Evaluate the AUTHOR TEXT against the provided FRAMEWORK descriptors, making critical notes of how the author's synthesis interacts with the REFERENCE SOURCES extractions.
2. SELECT the TOP 5 descriptors from the FRAMEWORK that most reflect the overall text. Ensure the TOP 5 represent distinct aspects of the diction (e.g., avoid selecting multiple variations of "Formal" if other textures are present).
3. SELECT the BOTTOM 5 descriptors from the FRAMEWORK that least reflect the overall text or are entirely absent, focusing on styles that are conspicuously absent given the subject matter.
4. ASSIGN SCORES: For each of the top and bottom selected values, assign a score reflecting the intensity/presence of the value: VERYLOW, LOW, MEDIUM, HIGH, or VERYHIGH. 
5. DRAFT REASONING: Provide at least two sentences of specific reasoning for each selection. Use evidence from the text's word choice, thematic weight, and stylistic choices, and how the text mimics or avoids specific styles.

# AUTHOR TEXT
{__AUTHOR_TEXT}

# REFERENCE SOURCES
{__REFERENCE_SOURCES}

# FRAMEWORK
{__FRAMEWORK}

# FORMAT
The output must be a syntax-correct YAML string with the following schema.
This YAML requires all strings to be surrounded by double quotes.
Do not include conversational filler or introductory text or code block quotes.

dimension: {__DIMENSION}
analysis:
  top_reflected_values:
    - value: "[Value Name]"
      score: "[SCORE]"
      reasoning: "[Sentence 1]. [Sentence 2]. ..."
    - (repeat for 5)
  bottom_reflected_values:
    - value: "[Value Name]"
      score: "[SCORE]"
      reasoning: "[Sentence 1]. [Sentence 2]. ..."
    - (repeat for 5)
"""

dimensions = [
    "writing.dimensions.0000.diction.yaml",
    "writing.dimensions.0001.rhythm.yaml",
    "writing.dimensions.0002.intertextuality.yaml",
    "writing.dimensions.0003.vibe.yaml",
    "writing.dimensions.0004.parallels.yaml",
    "writing.dimensions.0005.drops.yaml",
    "writing.dimensions.0006.ending.yaml",
]


