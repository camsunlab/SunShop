## Only need once at the start to connect the mysql databse

import mysql.connector

dataBase = mysql.connector.connect(
    host = "localhost",
    user = "root",
    password = "helloworld2023",
)

# prepare cursor object
cursorObject = dataBase.cursor()

# create database
cursorObject.execute("CREATE DATABASE sunshoppurchase")

print("Database created")

