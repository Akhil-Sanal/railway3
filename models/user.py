from app import db

class User(db.Model):

    __tablename__ = "users"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    username = db.Column(
        db.String(100)
    )

    password_hash = db.Column(
        db.Text
    )

    role = db.Column(
        db.String(20)
    )

    department_id = db.Column(
        db.Integer
    )