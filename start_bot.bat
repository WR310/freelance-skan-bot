@echo off
title Скайнет - Фриланс Сканер
color 0A
echo ===================================================
echo       ЗАПУСК ИИ-СКАНЕРА ФРИЛАНС-БИРЖ (SKYNET)
echo ===================================================
echo Источники: FL.ru, Kwork, Freelancium, Work24
echo.

:: Переходим в папку с твоим проектом
cd /d "C:\Users\GPC\project\botscan\freelance-skan-bot"

:: Запускаем скрипт
python main.py

:: Если скрипт упадет, окно не закроется, и ты сможешь прочитать ошибку
pause
