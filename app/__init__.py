# This file makes the 'app' directory a Python package.

# Ensure the main app module (app.py) is imported so Flask CLI can find the 'app' instance
# and registered commands like 'init-db'.
# When FLASK_APP=app, Flask imports the 'app' package (this __init__.py).
# It then looks for an 'app' instance. By importing .app (app.py),
# the 'app' instance defined in app.py becomes available via the loaded modules.

from . import app as app_module # This ensures app.py is loaded.
                                # The 'app' instance is app_module.app
                                # Flask should find 'app_module.app'
                                # Or, more simply, Flask's default search for app.app.py might kick in
                                # once app.py is part of the imported package structure.

# A more common way for Flask CLI to find the app instance is if app.py itself
# is named e.g. main.py and __init__.py does `from .main import app`.
# Or if app.py defines create_app() and __init__.py calls it.

# Let's try to ensure app.py's app object is the one Flask finds.
# One way:
# from .app import app
# This would make app.app (the instance from app.py) available as app package's 'app' attribute.
# This is often what's needed.

from .app import app # Expose the app instance from app.py at the package level.
