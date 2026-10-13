from datetime import datetime
from zoneinfo import ZoneInfo

import os
import sqlite3
import hashlib
import io
import json
import re

import requests
import speech_recognition as sr
import streamlit as st


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="AI Voice-to-SQL Database Assistant",
    page_icon="🎤",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_DATABASE_PATH = "database/students.db"
UPLOAD_DIRECTORY = "database/uploads"

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "llama3.2"


# ============================================================
# SESSION STATE
# ============================================================

if "database_path" not in st.session_state:
    st.session_state.database_path = DEFAULT_DATABASE_PATH

if "uploaded_database_hash" not in st.session_state:
    st.session_state.uploaded_database_hash = ""

if "database_name" not in st.session_state:
    st.session_state.database_name = "students.db"

if "question" not in st.session_state:
    st.session_state.question = ""

if "sql" not in st.session_state:
    st.session_state.sql = ""

if "results" not in st.session_state:
    st.session_state.results = []

if "columns" not in st.session_state:
    st.session_state.columns = []

if "error" not in st.session_state:
    st.session_state.error = ""

if "last_audio_id" not in st.session_state:
    st.session_state.last_audio_id = ""


# ============================================================
# ACTIVE DATABASE
# ============================================================

def get_database_path():
    return st.session_state.database_path


def database_exists():
    return os.path.exists(get_database_path())


# ============================================================
# DEFAULT DATABASE INFORMATION
# ============================================================

DATABASE_SCHEMA = """
TABLE: students

COLUMNS:
id
name
gender
department_id
year
email
city
date_of_birth

TABLE: marks

COLUMNS:
mark_id
student_id
course_id
marks
exam_type
exam_date

IMPORTANT RELATIONSHIP:
students.id = marks.student_id
"""


# ============================================================
# DATABASE UPLOAD
# ============================================================

def handle_database_upload(uploaded_file):
    """
    Save the uploaded SQLite database locally and make it
    the active database for the application.
    """

    if uploaded_file is None:
        return False

    file_bytes = uploaded_file.getvalue()

    if not file_bytes:
        st.error("The uploaded database is empty.")
        return False

    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # Do nothing if this exact database is already active.
    if file_hash == st.session_state.uploaded_database_hash:
        return False

    os.makedirs(UPLOAD_DIRECTORY, exist_ok=True)

    original_name = uploaded_file.name
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", original_name)

    # Hash-based name prevents old uploads from overwriting each other.
    saved_name = f"{file_hash[:16]}_{safe_name}"
    saved_path = os.path.join(UPLOAD_DIRECTORY, saved_name)

    with open(saved_path, "wb") as file:
        file.write(file_bytes)

    # Verify that SQLite can open the uploaded file.
    try:
        connection = sqlite3.connect(saved_path)
        cursor = connection.cursor()

        cursor.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            AND name NOT LIKE 'sqlite_%'
        """)

        tables = cursor.fetchall()
        connection.close()

    except sqlite3.Error as error:
        try:
            os.remove(saved_path)
        except OSError:
            pass

        st.error(
            f"Invalid SQLite database: {error}"
        )
        return False

    if not tables:
        st.error(
            "The uploaded SQLite database contains no tables."
        )
        return False

    # Make the uploaded database active.
    st.session_state.database_path = saved_path
    st.session_state.database_name = original_name
    st.session_state.uploaded_database_hash = file_hash

    # Clear old query/result state because it belongs to the old DB.
    st.session_state.question = ""
    st.session_state.sql = ""
    st.session_state.results = []
    st.session_state.columns = []
    st.session_state.error = ""
    st.session_state.last_audio_id = ""

    create_history_table()

    return True


# ============================================================
# QUERY HISTORY TABLE
# ============================================================

def create_history_table():
    database_path = get_database_path()

    if not os.path.exists(database_path):
        return

    try:
        connection = sqlite3.connect(database_path)
        cursor = connection.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS query_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                sql_query TEXT NOT NULL,
                result_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        connection.commit()
        connection.close()

    except sqlite3.Error:
        pass


create_history_table()


# ============================================================
# QUERY HISTORY
# ============================================================

def load_query_history():
    database_path = get_database_path()

    if not os.path.exists(database_path):
        return []

    create_history_table()

    connection = sqlite3.connect(database_path)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            question,
            sql_query,
            result_count,
            created_at
        FROM query_history
        ORDER BY id DESC
        LIMIT 30
    """)

    rows = cursor.fetchall()
    connection.close()

    return rows


def save_query_history(question, sql_query, result_count):
    database_path = get_database_path()

    if not os.path.exists(database_path):
        return

    create_history_table()

    connection = sqlite3.connect(database_path)
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO query_history
        (
            question,
            sql_query,
            result_count
        )
        VALUES (?, ?, ?)
        """,
        (
            question,
            sql_query,
            result_count,
        ),
    )

    connection.commit()
    connection.close()


def delete_all_history():
    database_path = get_database_path()

    if not os.path.exists(database_path):
        return

    create_history_table()

    connection = sqlite3.connect(database_path)
    cursor = connection.cursor()

    cursor.execute("DELETE FROM query_history")

    connection.commit()
    connection.close()


# ============================================================
# UTC -> IST
# ============================================================

def format_ist_time(created_at):
    if not created_at:
        return "Unknown time"

    try:
        utc_time = datetime.strptime(
            str(created_at),
            "%Y-%m-%d %H:%M:%S",
        )

        utc_time = utc_time.replace(
            tzinfo=ZoneInfo("UTC")
        )

        ist_time = utc_time.astimezone(
            ZoneInfo("Asia/Kolkata")
        )

        return ist_time.strftime(
            "%d %b %Y, %I:%M %p"
        )

    except Exception:
        return str(created_at)


# ============================================================
# GET REAL DATABASE SCHEMA
# ============================================================

def get_database_schema():
    database_path = get_database_path()

    if not os.path.exists(database_path):
        return []

    connection = sqlite3.connect(database_path)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)

    tables = cursor.fetchall()

    schema = []

    for table in tables:
        table_name = table[0]

        if table_name == "query_history":
            continue

        cursor.execute(
            f'PRAGMA table_info("{table_name}")'
        )

        columns = cursor.fetchall()

        column_names = [
            column[1]
            for column in columns
        ]

        schema.append(
            {
                "table": table_name,
                "columns": column_names,
            }
        )

    connection.close()

    return schema


# ============================================================
# SCHEMA TEXT FOR LLAMA
# ============================================================

def get_schema_text():
    schema = get_database_schema()

    if not schema:
        return DATABASE_SCHEMA

    schema_text = ""

    for table in schema:
        schema_text += (
            f"TABLE: {table['table']}\n"
        )

        schema_text += (
            "COLUMNS: "
            + ", ".join(table["columns"])
            + "\n\n"
        )

    return schema_text


# ============================================================
# CLEAN SQL
# ============================================================

def clean_sql(response):
    response = response.strip()

    response = re.sub(
        r"```sql",
        "",
        response,
        flags=re.IGNORECASE,
    )

    response = response.replace(
        "```",
        "",
    )

    response = response.strip()

    match = re.search(
        r"\b(SELECT|WITH)\b.*",
        response,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not match:
        raise ValueError(
            "Llama did not generate a SQL query."
        )

    sql = match.group(0).strip()

    sql = re.split(
        r"\n\s*(Explanation|Here is|This query|The query)",
        sql,
        flags=re.IGNORECASE,
    )[0]

    sql = sql.strip()

    if not sql.endswith(";"):
        sql += ";"

    return sql


# ============================================================
# SQL VALIDATION
# ============================================================

def validate_sql(sql):
    query = sql.strip().lower()

    if not (
        query.startswith("select")
        or query.startswith("with")
    ):
        raise ValueError(
            "Only SELECT queries are allowed."
        )

    dangerous_commands = [
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "create",
        "replace",
        "attach",
        "detach",
        "pragma",
        "vacuum",
        "reindex",
    ]

    for command in dangerous_commands:
        if re.search(
            rf"\b{command}\b",
            query,
        ):
            raise ValueError(
                f"Dangerous SQL blocked: {command}"
            )

    body = sql.rstrip().rstrip(";")

    if ";" in body:
        raise ValueError(
            "Multiple SQL statements are not allowed."
        )


# ============================================================
# CHECK SQL BEFORE EXECUTION
# ============================================================

def check_sql(sql):
    validate_sql(sql)

    database_path = get_database_path()

    if not os.path.exists(database_path):
        return False, "Database file not found."

    connection = sqlite3.connect(database_path)
    cursor = connection.cursor()

    try:
        cursor.execute(
            "EXPLAIN QUERY PLAN " + sql
        )

        return True, ""

    except sqlite3.Error as error:
        return False, str(error)

    finally:
        connection.close()


# ============================================================
# LLAMA SQL GENERATOR
# ============================================================

def generate_sql(question):
    schema = get_schema_text()

    prompt = f"""
You are an expert SQLite Text-to-SQL assistant.

Convert the user's natural-language question
into exactly ONE valid SQLite SQL query.

DATABASE SCHEMA:

{schema}

============================================================
RULES
============================================================

1. Return ONLY SQL.

2. Do not provide explanations.

3. Do not use markdown.

4. Use ONLY tables that exist in the schema.

5. Use ONLY columns that exist in the schema.

6. Never invent tables.

7. Never invent columns.

8. Every column must belong to the correct table.

9. Use correct table aliases.

10. Use correct JOIN relationships when relationships
    are available in the schema.

11. Database engine is SQLite.

12. Only SELECT or WITH queries are allowed.

13. Use COUNT for counting.

14. Use AVG for averages.

15. Use SUM for totals.

16. Use MIN for minimum.

17. Use MAX for maximum.

18. Use DISTINCT for unique values.

19. Use GROUP BY when required.

20. Use HAVING when required.

21. Use ORDER BY when required.

22. Use LIMIT for top N or bottom N.

23. Use LIKE for text searches.

24. Use BETWEEN for ranges.

25. Use IN when appropriate.

26. Use subqueries when appropriate.

27. Return exactly ONE SQL query.

28. If the question asks about a column,
    use the table that actually contains that column.

29. If the question requires information from multiple
    tables, use an appropriate JOIN.

30. Do not assume that the database contains students,
    marks, employees, projects, or any other specific
    table unless it exists in the schema.

USER QUESTION:

{question}

SQL:
"""

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0,
        },
    }

    try:
        response = requests.post(
            OLLAMA_URL,
            json=payload,
            timeout=120,
        )

    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            "Ollama is not running. Start Ollama first."
        )

    except requests.exceptions.Timeout:
        raise RuntimeError(
            "Ollama took too long to respond."
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"Ollama error: {response.text}"
        )

    data = response.json()

    answer = data.get(
        "response",
        "",
    )

    if not answer:
        raise RuntimeError(
            "Ollama returned an empty response."
        )

    # IMPORTANT: closing parenthesis is present.
    sql = clean_sql(answer)

    validate_sql(sql)

    valid, error = check_sql(sql)

    if not valid:
        raise RuntimeError(
            f"Generated SQL is invalid:\n{error}"
        )

    return sql


# ============================================================
# EXECUTE SQL
# ============================================================

def execute_sql(sql):
    validate_sql(sql)

    database_path = get_database_path()

    if not os.path.exists(database_path):
        raise RuntimeError(
            "Database file not found."
        )

    connection = sqlite3.connect(database_path)
    cursor = connection.cursor()

    try:
        cursor.execute(sql)

        rows = cursor.fetchall()

        if cursor.description:
            columns = [
                column[0]
                for column in cursor.description
            ]
        else:
            columns = []

        return columns, rows

    finally:
        connection.close()


# ============================================================
# SPEECH TO TEXT
# ============================================================

def convert_speech_to_text(audio):
    recognizer = sr.Recognizer()

    try:
        audio_bytes = audio.getvalue()

        audio_file = io.BytesIO(
            audio_bytes
        )

        with sr.AudioFile(audio_file) as source:
            audio_data = recognizer.record(source)

        text = recognizer.recognize_google(
            audio_data,
            language="en-US",
        )

        return text

    except sr.UnknownValueError:
        return None

    except sr.RequestError as error:
        raise RuntimeError(
            f"Speech recognition error: {error}"
        )


# ============================================================
# PROCESS QUESTION
# ============================================================

def process_question(question):
    question = question.strip()

    if not question:
        return

    st.session_state.question = question
    st.session_state.error = ""

    try:
        with st.spinner(
            "🧠 Llama 3.2 is generating SQL..."
        ):
            sql = generate_sql(question)

        st.session_state.sql = sql

        with st.spinner(
            "🗄️ Executing SQL..."
        ):
            columns, rows = execute_sql(sql)

        st.session_state.columns = columns
        st.session_state.results = rows

        save_query_history(
            question,
            sql,
            len(rows),
        )

    except Exception as error:
        st.session_state.error = str(error)


# ============================================================
# RESULT SPEECH
# ============================================================

def create_result_message():
    rows = st.session_state.results
    columns = st.session_state.columns

    if not rows:
        return "There are no results."

    if len(rows) == 1:
        if len(columns) == 1:
            return (
                f"The result is {rows[0][0]}."
            )

        values = ", ".join(
            str(value)
            for value in rows[0]
        )

        return f"The result is {values}."

    return (
        f"The query returned {len(rows)} results."
    )


def speak_result():
    message = create_result_message()

    safe_message = json.dumps(message)

    st.components.v1.html(
        f"""
        <script>
        const text = {safe_message};

        const speech =
            new SpeechSynthesisUtterance(text);

        speech.rate = 0.9;
        speech.pitch = 1;
        speech.volume = 1;

        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(speech);
        </script>
        """,
        height=1,
    )


# ============================================================
# CSS
# ============================================================

st.markdown(
    """
    <style>

    .stApp {
        background:
            radial-gradient(
                circle at 50% 40%,
                rgba(168,85,247,0.08),
                transparent 30%
            ),
            #07051A;
        color: #ffffff;
    }

    .block-container {
        max-width: 1500px;
        padding-top: 1rem;
        padding-bottom: 2rem;
    }

    .main-title {
        font-size: 36px;
        font-weight: 700;
        color: #ffffff;
        margin-bottom: 10px;
    }

    .main-title-accent {
        color: #A855F7;
    }

    .section-title {
        color: #A855F7;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 3px;
        text-transform: uppercase;
        margin-bottom: 18px;
    }

    .stButton > button {
        background: #11102B;
        color: #ffffff;
        border: 1px solid #3B2166;
        border-radius: 10px;
        min-height: 42px;
        transition: all 0.2s ease;
    }

    .stButton > button:hover {
        background: #21113D;
        color: #C084FC;
        border-color: #A855F7;
        box-shadow:
            0 0 15px
            rgba(168,85,247,0.25);
    }

    .stTextInput input {
        background: #11102B !important;
        color: #ffffff !important;
        border: 1px solid #3B2166 !important;
        border-radius: 10px !important;
        height: 48px !important;
    }

    .stTextInput input:focus {
        border-color: #A855F7 !important;
        box-shadow:
            0 0 12px
            rgba(168,85,247,0.25) !important;
    }

    .center-title {
        text-align: center;
        color: #A855F7;
        font-size: 11px;
        letter-spacing: 4px;
        font-weight: 700;
        margin-top: 30px;
    }

    .center-status {
        text-align: center;
        color: #ffffff;
        font-size: 30px;
        font-weight: 600;
        margin-top: 12px;
    }

    .center-description {
        text-align: center;
        color: #8B82A8;
        font-size: 13px;
        margin-top: 5px;
    }

    .orb {
        width: 300px;
        height: 300px;
        border-radius: 50%;
        margin: 40px auto 30px auto;
        background:
            radial-gradient(
                circle at 40% 35%,
                rgba(255,255,255,0.12),
                rgba(168,85,247,0.18) 30%,
                rgba(70,35,120,0.25) 45%,
                #07051A 75%
            );
        border: 1px solid #A855F7;
        box-shadow:
            0 0 25px rgba(168,85,247,0.60),
            0 0 65px rgba(126,34,206,0.35),
            inset 0 0 55px rgba(168,85,247,0.30);
        position: relative;
    }

    .orb:before {
        content: "";
        position: absolute;
        width: 120px;
        height: 120px;
        left: 50%;
        top: 50%;
        transform: translate(-50%, -50%);
        border-radius: 50%;
        background:
            radial-gradient(
                circle,
                rgba(168,85,247,0.42),
                transparent
            );
        box-shadow:
            0 0 45px
            rgba(168,85,247,0.60);
    }

    [data-testid="stCodeBlock"] {
        border: 1px solid #3B2166;
        border-radius: 9px;
    }

    [data-testid="stDataFrame"] {
        border: 1px solid #3B2166;
        border-radius: 9px;
    }

    hr {
        border-color: #25213A !important;
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# HEADER
# ============================================================

st.markdown(
    '<h1 class="main-title">'
    '<span class="main-title-accent">AI</span> '
    'Voice-to-SQL Database Assistant'
    '</h1>',
    unsafe_allow_html=True,
)

st.caption("Welcome")

st.divider()


# ============================================================
# THREE COLUMNS
# ============================================================

left, center, right = st.columns(
    [1.1, 2.2, 1.45],
    gap="large",
)


# ============================================================
# LEFT SIDE
# ============================================================

with left:

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    st.markdown(
        '<div class="section-title">DATABASE</div>',
        unsafe_allow_html=True,
    )

    st.caption("DATABASE")
    st.write("SQLite")

    schema = get_database_schema()

    if schema:
        # Show the first real table, exactly in the format requested.
        first_table = schema[0]

        st.caption("TABLE")
        st.write(first_table["table"])

        st.caption("COLUMNS")

        column_text = " • ".join(
            first_table["columns"]
        )

        st.write(column_text)

    else:
        st.caption("TABLE")
        st.write("No tables found.")

        st.caption("COLUMNS")
        st.write("No columns found.")

    st.divider()


    # --------------------------------------------------------
    # UPLOAD DATABASE
    # --------------------------------------------------------

    st.markdown(
        '<div class="section-title">UPLOAD DATABASE</div>',
        unsafe_allow_html=True,
    )

    st.write("Upload SQLite Database")

    uploaded_database = st.file_uploader(
        "Upload SQLite Database",
        type=["db", "sqlite", "sqlite3"],
        label_visibility="collapsed",
    )

    if uploaded_database is not None:

        new_hash = hashlib.sha256(
            uploaded_database.getvalue()
        ).hexdigest()

        if new_hash != st.session_state.uploaded_database_hash:

            if handle_database_upload(
                uploaded_database
            ):
                st.success(
                    f"{uploaded_database.name} loaded."
                )
                st.rerun()


    # --------------------------------------------------------
    # ACTIVE DATABASE NAME
    # --------------------------------------------------------

    st.caption(
        f"Active database: "
        f"{st.session_state.database_name}"
    )

    st.divider()


    # --------------------------------------------------------
    # QUERY HISTORY
    # --------------------------------------------------------

    with st.expander(
        "QUERY HISTORY",
        expanded=False,
    ):

        try:
            history_rows = load_query_history()

        except Exception as error:
            history_rows = []
            st.error(
                f"History error: {error}"
            )

        if history_rows:

            # Scrollable history list.
            with st.container(
                height=300,
                border=True,
            ):

                for record in history_rows:

                    history_id = record[0]
                    question = record[1]
                    saved_sql = record[2]
                    result_count = record[3]
                    created_at = record[4]

                    display_question = question

                    if len(display_question) > 40:
                        display_question = (
                            display_question[:40]
                            + "..."
                        )

                    if st.button(
                        display_question,
                        key=f"history_question_{history_id}",
                        width="stretch",
                    ):

                        try:
                            columns, rows = execute_sql(
                                saved_sql
                            )

                            st.session_state.question = question
                            st.session_state.sql = saved_sql
                            st.session_state.columns = columns
                            st.session_state.results = rows
                            st.session_state.error = ""

                            st.rerun()

                        except Exception as error:
                            st.error(str(error))

                    st.caption(
                        f"{format_ist_time(created_at)} • "
                        f"{result_count} result(s)"
                    )

        else:
            st.info("No queries yet.")


    # --------------------------------------------------------
    # CLEAR HISTORY
    # --------------------------------------------------------

    st.divider()

    if st.button(
        "🗑️ Clear History",
        key="clear_query_history_button",
        width="stretch",
    ):

        delete_all_history()

        st.session_state.question = ""
        st.session_state.sql = ""
        st.session_state.columns = []
        st.session_state.results = []
        st.session_state.error = ""

        st.rerun()


    st.divider()


    # --------------------------------------------------------
    # QUICK QUESTIONS
    # --------------------------------------------------------

    st.markdown(
        '<div class="section-title">QUICK QUESTIONS</div>',
        unsafe_allow_html=True,
    )

    quick_questions = [
        "Show the highest scorer",
        "Show the lowest scorer",
        "What is the average marks?",
        "How many students are there?",
        "Show students who scored above 90",
        "Show all students",
    ]

    for index, question in enumerate(
        quick_questions
    ):

        if st.button(
            question,
            key=f"quick_question_{index}",
            width="stretch",
        ):

            process_question(question)
            st.rerun()


# ============================================================
# CENTER
# ============================================================

with center:

    st.markdown(
        '<div class="center-title">'
        'VOICE DATABASE ASSISTANT'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="orb"></div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="center-status">'
        'Ready to Listen'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="center-description">'
        'Ask anything about your database.'
        '</div>',
        unsafe_allow_html=True,
    )

    st.write("")


    # --------------------------------------------------------
    # VOICE
    # --------------------------------------------------------

    audio = st.audio_input(
        "🎤 Click the microphone and speak",
        sample_rate=16000,
    )

    if audio:

        audio_id = hashlib.md5(
            audio.getvalue()
        ).hexdigest()

        if (
            audio_id
            != st.session_state.last_audio_id
        ):

            st.session_state.last_audio_id = audio_id

            with st.spinner(
                "🎧 Converting speech to text..."
            ):

                try:

                    voice_question = (
                        convert_speech_to_text(
                            audio
                        )
                    )

                    if voice_question:

                        st.success(
                            f"You said: "
                            f"{voice_question}"
                        )

                        process_question(
                            voice_question
                        )

                        st.rerun()

                    else:

                        st.warning(
                            "Sorry, I could not "
                            "understand your voice."
                        )

                except Exception as error:

                    st.error(
                        f"Audio processing error: "
                        f"{error}"
                    )


    st.divider()


    # --------------------------------------------------------
    # TEXT QUESTION
    # --------------------------------------------------------

    st.subheader("⌨️ Type your question")

    text_question = st.text_input(
        "Question",
        placeholder=(
            "Example: Show the highest scorer"
        ),
        label_visibility="collapsed",
    )

    if st.button(
        "🚀 Generate SQL",
        width="stretch",
    ):

        if text_question.strip():

            process_question(
                text_question
            )

            st.rerun()

        else:

            st.warning(
                "Please enter a question."
            )


# ============================================================
# RIGHT SIDE
# ============================================================

with right:

    # --------------------------------------------------------
    # GENERATED SQL
    # --------------------------------------------------------

    st.markdown(
        '<div class="section-title">'
        'GENERATED SQL'
        '</div>',
        unsafe_allow_html=True,
    )

    if st.session_state.sql:

        st.code(
            st.session_state.sql,
            language="sql",
        )

    else:

        st.info(
            "Waiting for a question..."
        )


    st.divider()


    # --------------------------------------------------------
    # RESULTS
    # --------------------------------------------------------

    st.markdown(
        '<div class="section-title">'
        'RESULTS'
        '</div>',
        unsafe_allow_html=True,
    )

    rows = st.session_state.results
    columns = st.session_state.columns

    if rows:

        st.success(
            f"Found {len(rows)} result(s)."
        )

        result_data = []

        for row in rows:

            result_data.append(
                dict(
                    zip(
                        columns,
                        row,
                    )
                )
            )

        st.dataframe(
            result_data,
            width="stretch",
            hide_index=True,
        )

        if st.button(
            "🔊 Speak Result",
            width="stretch",
        ):

            speak_result()

    else:

        st.info(
            "No results yet."
        )


    # --------------------------------------------------------
    # ERROR
    # --------------------------------------------------------

    if st.session_state.error:

        st.error(
            st.session_state.error
        )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "🎤 AI Voice-to-SQL • Llama 3.2 • SQLite"
)