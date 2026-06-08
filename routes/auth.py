from flask import Blueprint
from flask import render_template
from flask import request
from flask import redirect
from flask import session

auth_bp = Blueprint(
    "auth",
    __name__
)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]

        return f"Username: {username}"

    return render_template("login.html")