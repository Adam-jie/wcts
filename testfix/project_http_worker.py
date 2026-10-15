"""Start the actual Flaskr application from a specified built/checked directory."""
import argparse
import secrets
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--seed-demo", action="store_true")
    args = parser.parse_args()
    project = args.project.resolve()
    sys.path.insert(0, str(project))
    from flaskr import create_app
    from flaskr.db import init_db
    import flaskr
    if not Path(flaskr.__file__).resolve().is_relative_to(project):
        raise RuntimeError("Application import did not come from selected delivery")
    args.database.parent.mkdir(parents=True, exist_ok=True)
    fresh = not args.database.exists()
    app = create_app({"DATABASE": str(args.database.resolve()), "SECRET_KEY": secrets.token_hex(32), "TESTING": False})
    if fresh:
        with app.app_context():
            init_db()
            if args.seed_demo:
                from flaskr.db import get_db
                from werkzeug.security import generate_password_hash
                db = get_db()
                db.execute("INSERT INTO user(username,password) VALUES (?,?)", ("demo", generate_password_hash("only-local-demo")))
                for index in range(18):
                    title = f"Agent engineering notes {index + 1}" if index < 13 else f"Other topic {index + 1}"
                    db.execute("INSERT INTO post(title,body,author_id,created) VALUES (?,?,1,?)",
                               (title, f"This is a persistent demonstration article, number {index + 1}. Open its title to inspect the full detail page.", "2026-10-14 08:00:00"))
                db.commit()
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
