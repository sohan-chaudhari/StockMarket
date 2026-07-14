import database, models

def reset():
    print("Resetting database...")
    models.Base.metadata.drop_all(bind=database.engine)
    models.Base.metadata.create_all(bind=database.engine)
    print("Database reset complete.")

if __name__ == "__main__":
    reset()
