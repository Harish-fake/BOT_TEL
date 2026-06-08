import os
from pathlib import Path
from typing import Optional


TECH_DETECTORS: dict[str, list[str]] = {
    "Python": [".py", "requirements.txt", "setup.py", "Pipfile", "pyproject.toml"],
    "Java": [".java", "pom.xml", "build.gradle", "gradlew"],
    "JavaScript": [".js", ".jsx", "package.json", "webpack.config.js"],
    "TypeScript": [".ts", ".tsx", "tsconfig.json"],
    "React": [".jsx", ".tsx", "jsx-runtime", "react"],
    "Node.js": ["package.json", "node_modules", "server.js", "app.js"],
    "Spring Boot": ["pom.xml", "application.properties", "application.yml", "spring-boot"],
    "Flask": ["flask", "app.py", "wsgi.py"],
    "Django": ["django", "manage.py", "settings.py", "urls.py", "wsgi.py"],
    "HTML": [".html", ".htm"],
    "CSS": [".css", ".scss", ".sass", ".less"],
    "C++": [".cpp", ".cc", ".cxx", ".hpp", ".hxx"],
    "C#": [".cs", ".csproj", ".sln"],
    "PHP": [".php", ".phtml", "composer.json"],
    "Ruby": [".rb", "Gemfile", "Rakefile"],
    "Go": [".go", "go.mod", "go.sum"],
    "Rust": [".rs", "Cargo.toml"],
    "Kotlin": [".kt", ".kts", "build.gradle.kts"],
    "Swift": [".swift", "Package.swift"],
    "MongoDB": ["mongodb", "mongoose", "pymongo", "mongod"],
    "PostgreSQL": ["postgresql", "psycopg2", "pg", "postgres"],
    "Docker": ["Dockerfile", "docker-compose.yml", ".dockerignore"],
}

TEXT_EXTENSIONS: set = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cpp", ".c", ".h", ".hpp",
    ".cs", ".php", ".rb", ".go", ".rs", ".kt", ".swift", ".html", ".htm",
    ".css", ".scss", ".sass", ".less", ".xml", ".yml", ".yaml", ".json",
    ".md", ".txt", ".cfg", ".conf", ".ini", ".env", ".sh", ".bat", ".ps1",
    ".sql", ".gradle", ".tf", ".zig", ".toml", ".vue", ".svelte",
}


class ProjectAnalyzer:

    def analyze(self, project_path: str) -> dict:
        files_count = 0
        folders_count = 0
        loc = 0
        detected_techs: set[str] = set()
        all_files: list[str] = []

        for root, dirs, files in os.walk(project_path):
            folders_count += len(dirs)
            for file in files:
                files_count += 1
                fpath = os.path.join(root, file)
                all_files.append(fpath)

                ext = Path(file).suffix.lower()

                for tech, indicators in TECH_DETECTORS.items():
                    if ext in indicators or file in indicators or file.endswith(tuple(indicators)):
                        detected_techs.add(tech)

                if ext in TEXT_EXTENSIONS:
                    try:
                        with open(fpath, "r", errors="replace") as f:
                            for _ in f:
                                loc += 1
                    except Exception:
                        pass

        return {
            "files": files_count,
            "folders": folders_count,
            "loc": loc,
            "technologies": sorted(detected_techs),
        }

    @staticmethod
    def generate_gitignore(technologies: list[str]) -> str:
        templates: dict[str, str] = {
            "Python": "*.pyc\n__pycache__/\n*.pyo\n*.egg-info/\ndist/\nbuild/\n.eggs/\n*.egg\n.venv/\nvenv/\nenv/\n",
            "Node.js": "node_modules/\nnpm-debug.log*\nyarn-debug.log*\nyarn-error.log*\npackage-lock.json\ndist/\nbuild/\n.env\n",
            "Java": "*.class\n*.jar\n*.war\n*.nar\ntarget/\ndependency-reduced-pom.xml\nbuild/\n.settings/\n.project\n.classpath\n.idea/\n*.iml\n",
            "C++": "*.o\n*.obj\n*.exe\n*.out\n*.app\nbuild/\ncmake-build-*\n.idea/\n",
            "C#": "bin/\nobj/\n*.suo\n*.user\n*.vs/\nDebug/\nRelease/\npackages/\n",
            "React": "node_modules/\nbuild/\n*.js.map\n.env\n.env.local\n",
            "Django": "*.pyc\n__pycache__/\n*.pyo\n*.sqlite3\nstaticfiles/\nmedia/\n.venv/\n",
            "Flask": "*.pyc\n__pycache__/\n*.pyo\ninstance/\n.venv/\nenv/\n*.db\n",
        }

        lines = ["# GitSync Bot Auto-generated .gitignore\n"]
        added = set()
        for tech in sorted(technologies):
            for tname, content in templates.items():
                if tname in tech or tech in tname:
                    if tname not in added:
                        lines.append(f"# {tname}\n{content}\n")
                        added.add(tname)

        final = "".join(lines)
        if len(final.strip()) < 30:
            final += "*~\n*.swp\n*.swo\n*.bak\n.DS_Store\nThumbs.db\n"
        return final
