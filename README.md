# Chinese Elite (中国精英)

An experimental project, that automatically maps the relationship networks of Chinese elites by parsing public data using LLMs and cross-referencing with official sources.

---

## About The Project

This repository, **Chinese Elite**, aims to create a dynamic, self-updating, and publicly accessible database of the relationship networks among China's political and business elites. In a landscape where information can be fragmented and opaque, this project leverages the power of open-source Large Language Models (LLMs) to build a structured graph database from publicly available data, primarily Wikipedia/Wikidata.

The core goal is to provide a transparent, verifiable, and continuously improving tool for researchers, journalists, and anyone interested in understanding the power structures within China. By automating the data collection and relationship extraction process, we aim to create a resource that remains relevant over time, albeit with an acknowledged data lag inherent to its sources.

### How It Works: The Automated Method

Our methodology combines modern AI techniques with a commitment to verifiable sources:

1.  **Data Extraction**: The system periodically scans designated public sources (like Wikipedia pages of political figures, SOE executives, etc.) to extract biographical and career information.
2.  **LLM-Powered Relationship Parsing**: An open-source Large Language Model (e.g., models from the Qwen, Llama, Mistral, or Yi series) is used to parse the unstructured text. The LLM is prompted to identify and structure key relationships, such as:
    * **Professional Ties**: Superior/subordinate relationships, colleagues in a specific ministry or company.
    * **Educational Background**: Alumni of the same university or academy.
    * **Provincial/Factional Links**: Shared home provinces or common career paths.
    * **Family Connections**: Spouses, children, and other relatives.
3.  **Validation with Official Sources**: The relationships and career changes identified by the LLM are cross-referenced with official personnel announcements from Chinese sources. This helps validate the timeline and accuracy of appointments, removals, and transfers.
4.  **Graph Database Storage**: The validated entities (people, organizations) and their relationships are stored in a graph database (e.g., Neo4j), allowing for complex network analysis and queries.
5.  **Visualization**: A simple web interface allows users to search for individuals and visualize their network of connections.

### Project Status

This project is currently in the [**Proof-of-Concept / Development**] phase. We are focusing on building a stable data pipeline and refining the LLM prompting for accurate relationship extraction.

### Disclaimer

The data presented by this tool is for informational and research purposes only. It is automatically generated from public sources and is subject to the inherent delays and potential inaccuracies of those sources. All information should be independently verified using the provided official links or other primary sources.

### Contributing

This is an open-source effort, and contributions are highly welcome! Whether you are a developer, a data scientist, or a domain expert, you can help by:

* Improving the data extraction scripts.
* Fine-tuning the LLM prompts.
* Expanding the list of "seed" individuals and organizations.
* Enhancing the front-end visualization.
* Reporting inaccuracies and suggesting better data sources.

Please feel free to fork the repository, open an issue, or submit a pull request.
