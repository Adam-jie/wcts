import re

from flask import Blueprint
from flask import flash
from flask import g
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from werkzeug.exceptions import abort

from .auth import login_required
from .db import get_db

bp = Blueprint("blog", __name__)

POSTS_PER_PAGE = 5
ASCII_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*\Z")
ASCII_LOWERCASE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
)


@bp.route("/")
def index():
    """Show a searchable, paginated list of posts, most recent first."""
    page_value = request.args.get("page")
    if page_value is None:
        page_value = "1"
    elif not ASCII_POSITIVE_INTEGER.fullmatch(page_value):
        abort(400)

    query = request.args.get("q", "").strip()
    escaped_query = (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )

    db = get_db()
    where = ""
    parameters = []
    if query:
        where = " WHERE LOWER(title) LIKE ? ESCAPE '\\'"
        parameters.append(f"%{escaped_query.translate(ASCII_LOWERCASE)}%")

    total = db.execute(
        "SELECT COUNT(*) FROM post" + where,
        parameters,
    ).fetchone()[0]
    last_page = (total + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
    maximum_page = str(max(last_page, 1))
    if (len(page_value), page_value) > (len(maximum_page), maximum_page):
        abort(404)
    page = int(page_value)

    posts = db.execute(
        "SELECT p.id, title, body, created, author_id, username"
        " FROM post p JOIN user u ON p.author_id = u.id"
        + where
        + " ORDER BY created DESC, p.id DESC LIMIT ? OFFSET ?",
        (*parameters, POSTS_PER_PAGE, (page - 1) * POSTS_PER_PAGE),
    ).fetchall()
    return render_template(
        "blog/index.html",
        posts=posts,
        query=query,
        page=page,
        last_page=last_page,
    )


def get_post(id, check_author=True):
    """Get a post and its author by id.

    Checks that the id exists and optionally that the current user is
    the author.

    :param id: id of post to get
    :param check_author: require the current user to be the author
    :return: the post with author information
    :raise 404: if a post with the given id doesn't exist
    :raise 403: if the current user isn't the author
    """
    post = (
        get_db()
        .execute(
            "SELECT p.id, title, body, created, author_id, username"
            " FROM post p JOIN user u ON p.author_id = u.id"
            " WHERE p.id = ?",
            (id,),
        )
        .fetchone()
    )

    if post is None:
        abort(404, f"Post id {id} doesn't exist.")

    if check_author and post["author_id"] != g.user["id"]:
        abort(403)

    return post


@bp.route("/<int:id>")
def detail(id):
    """Show one post publicly without granting modification rights."""
    return render_template("blog/detail.html", post=get_post(id, check_author=False))


@bp.route("/create", methods=("GET", "POST"))
@login_required
def create():
    """Create a new post for the current user."""
    if request.method == "POST":
        title = request.form["title"]
        body = request.form["body"]
        error = None

        if not title:
            error = "Title is required."

        if error is not None:
            flash(error)
        else:
            db = get_db()
            db.execute(
                "INSERT INTO post (title, body, author_id) VALUES (?, ?, ?)",
                (title, body, g.user["id"]),
            )
            db.commit()
            return redirect(url_for("blog.index"))

    return render_template("blog/create.html")


@bp.route("/<int:id>/update", methods=("GET", "POST"))
@login_required
def update(id):
    """Update a post if the current user is the author."""
    post = get_post(id)

    if request.method == "POST":
        title = request.form["title"]
        body = request.form["body"]
        error = None

        if not title:
            error = "Title is required."

        if error is not None:
            flash(error)
        else:
            db = get_db()
            db.execute(
                "UPDATE post SET title = ?, body = ? WHERE id = ?", (title, body, id)
            )
            db.commit()
            return redirect(url_for("blog.index"))

    return render_template("blog/update.html", post=post)


@bp.route("/<int:id>/delete", methods=("POST",))
@login_required
def delete(id):
    """Delete a post.

    Ensures that the post exists and that the logged in user is the
    author of the post.
    """
    get_post(id)
    db = get_db()
    db.execute("DELETE FROM post WHERE id = ?", (id,))
    db.commit()
    return redirect(url_for("blog.index"))
