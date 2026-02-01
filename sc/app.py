from flask import Flask, render_template, request,session,redirect
import sqlite3
import os
from datetime import date, timedelta
app = Flask(__name__)
app.secret_key = "school_canteen_secret"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")

# Нормы по возрастным группам (завтрак + обед)
NORMS = {
    "10-12": {"calories": (800, 1000), "proteins": (30, 45), "fats": (25, 35), "carbs": (100, 140)},
    "13-15": {"calories": (900, 1100), "proteins": (35, 55), "fats": (30, 40), "carbs": (120, 160)},
    "16-18": {"calories": (1000, 1300), "proteins": (45, 70), "fats": (35, 45), "carbs": (150, 200)}
}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # таблица меню
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS menu (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        category TEXT,
        price REAL,
        calories INTEGER,
        proteins INTEGER,
        fats INTEGER,
        carbs INTEGER,
        contains_milk INTEGER DEFAULT 0
    )
    """)

    # таблица statistics
    cursor.execute("""
       CREATE TABLE IF NOT EXISTS statistics (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           student_code TEXT,
           age_group TEXT,
           food_type TEXT,
           calories INTEGER,
           proteins INTEGER,
           fats INTEGER,
           carbs INTEGER,
           date TEXT
       )
       """)

    # таблица аналитики блюд
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dish_statistics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dish_id INTEGER,
        meal_type TEXT,
        date TEXT
    )
    """)

    conn.commit()
    conn.close()


def save_statistics(age_group, food_type, totals):
    student_code = session.get("student_code")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO statistics (
        student_code, age_group, food_type,
        calories, proteins, fats, carbs, date
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        student_code,
        age_group,
        food_type,
        totals["calories"],
        totals["proteins"],
        totals["fats"],
        totals["carbs"],
        date.today().isoformat()
    ))

    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(DB_PATH)

def check_norm(value, min_val, max_val):
    if value < min_val:
        return "недостаточно"
    elif value > max_val:
        return "превышение"
    else:
        return "норма"

def calculate_bju_percentages(totals):
    calories_from_proteins = totals["proteins"] * 4
    calories_from_fats = totals["fats"] * 9
    calories_from_carbs = totals["carbs"] * 4
    total_calories = calories_from_proteins + calories_from_fats + calories_from_carbs
    if total_calories == 0:
        return {"proteins": 0, "fats": 0, "carbs": 0}
    return {
        "proteins": round(calories_from_proteins / total_calories * 100, 1),
        "fats": round(calories_from_fats / total_calories * 100, 1),
        "carbs": round(calories_from_carbs / total_calories * 100, 1)
    }

def check_milk(dish_ids, cursor):
    milk_dishes = []
    for dish_id in dish_ids:
        cursor.execute("SELECT name, contains_milk FROM menu WHERE id = ?", (dish_id,))
        name, contains_milk = cursor.fetchone()
        if contains_milk == 1:
            milk_dishes.append(name)
    return milk_dishes

def generate_summary(evaluation, food_type, totals, milk_violations):
    messages = []

    # Основные замечания
    base_messages = {
        "calories": {"недостаточно": "Недостаточная калорийность рациона.", "превышение": "Превышена рекомендуемая калорийность."},
        "proteins": {"недостаточно": "Недостаточно белка.", "превышение": "Слишком много белка."},
        "fats": {"недостаточно": "Недостаточно жиров.", "превышение": "Превышено содержание жиров."},
        "carbs": {"недостаточно": "Недостаточно углеводов.", "превышение": "Превышено содержание углеводов."}
    }
    for key, status in evaluation.items():
        if status != "норма":
            messages.append(base_messages[key][status])

    # Ограничения и рекомендации по типу питания
    if food_type == "спортивное" and totals["proteins"] < 50:
        messages.append("Для спортивного питания рекомендуется увеличить количество белка.")
    elif food_type == "диетическое" and totals["calories"] > 1000:
        messages.append("Для диетического питания рекомендуется снизить калорийность.")
    elif food_type == "безмолочное":
        messages.append("Рацион подобран с учётом безмолочного питания.")
        if milk_violations:
            messages.append("Обнаружены блюда с молочными продуктами: " + ", ".join(milk_violations))
    elif food_type == "классическое":
        messages.append("Рацион соответствует классическому типу питания.")

    if not messages:
        return "Рацион полностью соответствует нормам и выбранному типу питания."

    return " ".join(messages)

#Маршруты
@app.route("/")
def categories():
    categories = ["классическое", "диетическое", "безмолочное", "спортивное"]
    return render_template("category.html", categories=categories)

@app.route("/menu/<category>")
def menu(category):
    if "student_code" not in session:
        return redirect("/")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, name, price, calories FROM menu WHERE category = ?",
        (category,)
    )

    dishes = cursor.fetchall()  # ← ТОЛЬКО ОДИН РАЗ
    conn.close()

    return render_template(
        "menu.html",
        dishes=dishes,
        category=category
    )

@app.route("/result", methods=["POST"])
def result():
    student_code = session.get("student_code")
    if not student_code:
        return redirect("/")
    breakfast_ids = request.form.getlist("breakfast")
    lunch_ids = request.form.getlist("lunch")
    age_group = request.form["age_group"]
    food_type = request.form["food_type"]


    db = get_db()
    cursor = db.cursor()

    totals = {"price":0, "calories":0, "proteins":0, "fats":0, "carbs":0}
    breakfast = []
    lunch = []

    def add_dish(dish_id, meal_list):
        cursor.execute("SELECT name, price, calories, proteins, fats, carbs FROM menu WHERE id = ?", (dish_id,))
        dish = cursor.fetchone()
        meal_list.append(dish[0])
        totals["price"] += dish[1]
        totals["calories"] += dish[2]
        totals["proteins"] += dish[3]
        totals["fats"] += dish[4]
        totals["carbs"] += dish[5]

    for d in breakfast_ids:
        add_dish(d, breakfast)
    for d in lunch_ids:
        add_dish(d, lunch)

    milk_violations = []
    if food_type == "безмолочное":
        all_dishes = breakfast_ids + lunch_ids
        milk_violations = check_milk(all_dishes, cursor)

    db.close()

    # Оценка норм
    evaluation = {
        "calories": check_norm(totals["calories"], *NORMS[age_group]["calories"]),
        "proteins": check_norm(totals["proteins"], *NORMS[age_group]["proteins"]),
        "fats": check_norm(totals["fats"], *NORMS[age_group]["fats"]),
        "carbs": check_norm(totals["carbs"], *NORMS[age_group]["carbs"])
    }

    bju_percent = calculate_bju_percentages(totals)
    summary = generate_summary(evaluation, food_type, totals, milk_violations)
    save_statistics(age_group, food_type, totals)



    return render_template(
        "result.html",
        breakfast=breakfast,
        lunch=lunch,
        totals=totals,
        evaluation=evaluation,
        summary=summary,
        age_group=age_group,
        food_type=food_type,
        bju_percent=bju_percent,
        milk_violations=milk_violations
    )

@app.route("/statistics")
def statistics():
    student_code = session.get("student_code")
    if not student_code:
        return "Сначала введите код ученика"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    start_date = (date.today() - timedelta(days=7)).isoformat()

    cursor.execute("""
        SELECT calories, proteins, fats, carbs
        FROM statistics
        WHERE student_code = ? AND date >= ?
    """, (student_code, start_date))

    rows = cursor.fetchall()
    conn.close()

    count = len(rows)
    averages = {"calories": 0, "proteins": 0, "fats": 0, "carbs": 0}

    if count > 0:
        for r in rows:
            averages["calories"] += r[0]
            averages["proteins"] += r[1]
            averages["fats"] += r[2]
            averages["carbs"] += r[3]

        for key in averages:
            averages[key] = round(averages[key] / count, 1)

    return render_template("statistics.html", averages=averages, count=count)
@app.route("/set-student", methods=["POST"])
def set_student():
    session["student_code"] = request.form["student_code"]
    return redirect("/")

if __name__ == "__main__":
    init_db()
    app.run(debug=True)