# ArchiGent - Agentic IFC Workflow

ArchiGent is an AI-powered BIM agent that analyzes and modifies IFC files using LangGraph and Gemini.

## Setup
1. Create a `.env` file from `.env.example`.
2. Install dependencies:
   ```bash
   uv pip install -r requirements.txt
   ```
3. Run Neo4j (default: bolt://localhost:7687).

## Usage
Run the main script with a natural language request:
```bash
python main.py --request "모든 벽의 이름을 조회해줘"
```
If you don't specify `--ifc`, you can select one from the `raw/` directory interactively.

## Pipeline
1. **Analyzer**: Extract context using Cypher queries.
2. **Planner**: Generate 3 architectural options (for modifications).
3. **Coder**: Generate and run `ifcopenshell` code.
4. **Reviewer/Verifier**: Ensure safety and accuracy.