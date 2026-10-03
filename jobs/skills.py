"""
jobs/skills.py — a shared vocabulary of skills, so a resume and a job description
are read the same way ("ReactJS", "React.js" and "React" are one skill).

Generic on purpose: it is a list of technologies and practices, not anyone's
profile. Words that are also plain English ("Go", "R", "C", "Rust", "Swift",
"Spring") only count where the text clearly means the technology.
"""
from __future__ import annotations

import re

# canonical name → (category, aliases)
SKILLS: dict[str, tuple[str, tuple[str, ...]]] = {
    # languages
    "Python": ("language", ("python", "python3")),
    "Java": ("language", ("java",)),
    "JavaScript": ("language", ("javascript", "js", "ecmascript", "es6")),
    "TypeScript": ("language", ("typescript",)),
    "C++": ("language", ("c++", "cpp")),
    "C#": ("language", ("c#", "csharp")),
    "C": ("language", ()),
    "Go": ("language", ("golang",)),
    "Rust": ("language", ()),
    "Kotlin": ("language", ("kotlin",)),
    "Swift": ("language", ()),
    "Ruby": ("language", ("ruby",)),
    "PHP": ("language", ("php",)),
    "Scala": ("language", ("scala",)),
    "R": ("language", ()),
    "Dart": ("language", ("dart",)),
    "SQL": ("language", ("sql",)),
    "Bash": ("language", ("bash", "shell scripting")),
    "HTML": ("language", ("html", "html5")),
    "CSS": ("language", ("css", "css3")),
    "MATLAB": ("language", ("matlab",)),
    # frameworks / libraries
    "React": ("framework", ("react", "reactjs", "react.js")),
    "React Native": ("framework", ("react native",)),
    "Next.js": ("framework", ("next.js", "nextjs")),
    "Angular": ("framework", ("angular", "angularjs")),
    "Vue": ("framework", ("vue", "vue.js", "vuejs")),
    "Node.js": ("framework", ("node.js", "nodejs", "node")),
    "Express": ("framework", ("express", "express.js", "expressjs")),
    "Django": ("framework", ("django",)),
    "Flask": ("framework", ("flask",)),
    "FastAPI": ("framework", ("fastapi",)),
    "Spring": ("framework", ("spring boot", "springboot", "spring framework")),
    ".NET": ("framework", (".net", "dotnet", "asp.net")),
    "Laravel": ("framework", ("laravel",)),
    "Rails": ("framework", ("ruby on rails", "rails")),
    "Flutter": ("framework", ("flutter",)),
    "Tailwind CSS": ("framework", ("tailwind", "tailwindcss", "tailwind css")),
    "Bootstrap": ("framework", ("bootstrap",)),
    "jQuery": ("framework", ("jquery",)),
    "GraphQL": ("framework", ("graphql",)),
    "REST APIs": ("framework", ("restful", "rest api", "rest apis", "restful apis", "rest services")),
    "gRPC": ("framework", ("grpc",)),
    "TensorFlow": ("framework", ("tensorflow",)),
    "PyTorch": ("framework", ("pytorch", "torch")),
    "Keras": ("framework", ("keras",)),
    "scikit-learn": ("framework", ("scikit-learn", "sklearn", "scikit learn")),
    "Pandas": ("framework", ("pandas",)),
    "NumPy": ("framework", ("numpy",)),
    "OpenCV": ("framework", ("opencv",)),
    "LangChain": ("framework", ("langchain",)),
    "Hugging Face": ("framework", ("hugging face", "huggingface", "transformers")),
    "Spark": ("framework", ("apache spark", "pyspark", "spark")),
    "Hadoop": ("framework", ("hadoop",)),
    "Kafka": ("framework", ("kafka", "apache kafka")),
    "Selenium": ("framework", ("selenium",)),
    "Playwright": ("framework", ("playwright",)),
    "Jest": ("framework", ("jest",)),
    "Pytest": ("framework", ("pytest",)),
    "JUnit": ("framework", ("junit",)),
    "Redux": ("framework", ("redux",)),
    "Celery": ("framework", ("celery",)),
    # databases
    "PostgreSQL": ("database", ("postgresql", "postgres", "psql")),
    "MySQL": ("database", ("mysql",)),
    "MongoDB": ("database", ("mongodb", "mongo")),
    "Redis": ("database", ("redis",)),
    "SQLite": ("database", ("sqlite",)),
    "Oracle": ("database", ("oracle db", "oracle database", "pl/sql")),
    "SQL Server": ("database", ("sql server", "mssql")),
    "Cassandra": ("database", ("cassandra",)),
    "DynamoDB": ("database", ("dynamodb",)),
    "Elasticsearch": ("database", ("elasticsearch", "elastic search", "opensearch")),
    "Firebase": ("database", ("firebase", "firestore")),
    "Supabase": ("database", ("supabase",)),
    "Snowflake": ("database", ("snowflake",)),
    "BigQuery": ("database", ("bigquery",)),
    # cloud / devops / tools
    "AWS": ("cloud", ("aws", "amazon web services", "ec2", "aws lambda")),
    "Azure": ("cloud", ("azure", "microsoft azure")),
    "GCP": ("cloud", ("gcp", "google cloud", "google cloud platform")),
    "Docker": ("cloud", ("docker", "containerization")),
    "Kubernetes": ("cloud", ("kubernetes", "k8s")),
    "Terraform": ("cloud", ("terraform",)),
    "Ansible": ("cloud", ("ansible",)),
    "Jenkins": ("cloud", ("jenkins",)),
    "GitHub Actions": ("cloud", ("github actions",)),
    "CI/CD": ("cloud", ("ci/cd", "ci cd", "continuous integration", "continuous deployment")),
    "Linux": ("cloud", ("linux", "unix")),
    "Nginx": ("cloud", ("nginx",)),
    "Git": ("tool", ("git", "gitlab", "bitbucket")),
    "Jira": ("tool", ("jira",)),
    "Figma": ("tool", ("figma",)),
    "Postman": ("tool", ("postman",)),
    "Tableau": ("tool", ("tableau",)),
    "Power BI": ("tool", ("power bi", "powerbi")),
    "Excel": ("tool", ("excel", "ms excel")),
    "Airflow": ("tool", ("airflow", "apache airflow")),
    # practices / domains
    "Machine Learning": ("practice", ("machine learning", "ml")),
    "Deep Learning": ("practice", ("deep learning",)),
    "NLP": ("practice", ("nlp", "natural language processing")),
    "Computer Vision": ("practice", ("computer vision",)),
    "Generative AI": ("practice", ("generative ai", "genai", "gen ai", "llm", "llms", "large language models")),
    "Data Analysis": ("practice", ("data analysis", "data analytics")),
    "Data Structures": ("practice", ("data structures", "dsa", "data structures and algorithms")),
    "Algorithms": ("practice", ("algorithms",)),
    "System Design": ("practice", ("system design",)),
    "Microservices": ("practice", ("microservices", "micro-services", "microservice")),
    "OOP": ("practice", ("oop", "object oriented", "object-oriented")),
    "Agile": ("practice", ("agile", "scrum")),
    "Unit Testing": ("practice", ("unit testing", "unit tests", "test automation")),
    "DevOps": ("practice", ("devops",)),
    "Distributed Systems": ("practice", ("distributed systems",)),
    "Web Development": ("practice", ("web development", "full stack", "full-stack", "fullstack")),
    "Android": ("practice", ("android",)),
    "iOS": ("practice", ("ios",)),
}

# Plain-English names: they count only in a technical setting — next to another
# skill in a list, or in phrases like "Go developer" / "programming in R".
_AMBIGUOUS = {"C": r"(?:\bC(?=\s*(?:/|,|\bprogramming\b|\blanguage\b)|\s*$)|(?<=[,/(]\s)C\b|(?<=[,/(])C\b)(?![+#])",
              "Go": r"\bGo(?:lang)?\b(?=\s*(?:,|/|developer|engineer|programming|language))|(?<=[,/(]\s)Go\b",
              "R": r"(?<=[,/(]\s)R\b(?!&)|\bR(?=\s*(?:,|/|programming|language|studio))",
              "Rust": r"\bRust\b",
              "Swift": r"\bSwift\b(?!ly)",
              "Spring": r"\bSpring(?:\s*Boot)?\b(?!\s+(?:semester|term|internship|break))"}

_cache: list[tuple[str, re.Pattern]] | None = None


def _patterns() -> list[tuple[str, re.Pattern]]:
    global _cache
    if _cache is None:
        out = []
        for name, (_, aliases) in SKILLS.items():
            if name in _AMBIGUOUS:
                out.append((name, re.compile(_AMBIGUOUS[name])))
            terms = sorted({a for a in aliases} | ({name.lower()} if name not in _AMBIGUOUS else set()),
                           key=len, reverse=True)
            if terms:
                alt = "|".join(re.escape(t) for t in terms)
                out.append((name, re.compile(rf"(?<![A-Za-z0-9+#.])(?:{alt})(?![A-Za-z0-9+#])", re.I)))
        _cache = out
    return _cache


def find_skills(text: str) -> list[str]:
    """Canonical skills mentioned in `text`, in order of first appearance."""
    text = text or ""
    first: dict[str, int] = {}
    for name, pat in _patterns():
        m = pat.search(text)
        if m and (name not in first or m.start() < first[name]):
            first[name] = m.start()
    # "React Native" also mentions "React"; "Node.js" via "node" is noisy in prose — keep it only
    # when it isn't just the English word ("node in a graph")
    if "Node.js" in first and not re.search(r"node\.?js|nodejs|node\s*(?:backend|server|developer)", text, re.I):
        first.pop("Node.js")
    return sorted(first, key=first.get)


def category(name: str) -> str:
    return SKILLS.get(name, ("other", ()))[0]


def canonical(name: str) -> str:
    """'reactjs' → 'React'; unknown names come back tidied but unchanged."""
    found = find_skills(name)
    return found[0] if len(found) == 1 else " ".join((name or "").split())
