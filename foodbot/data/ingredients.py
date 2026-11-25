"""Base de datos de ingredientes: kcal y macros por 100 g + conversiones de unidades.

Los macros NO se le preguntan al modelo: se calculan de forma determinista a partir de
esta tabla, de modo que siempre sean coherentes con las calorias mostradas.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

# (kcal, proteina_g, carboidratos_g, grasa_g) por 100 g de producto.
INGREDIENTS: dict[str, tuple[float, float, float, float]] = {
    # --- proteinas -------------------------------------------------------
    "pechuga de pollo": (165, 31.0, 0.0, 3.6),
    "pollo": (215, 27.0, 0.0, 10.9),
    "muslo de pollo": (177, 24.8, 0.0, 7.2),
    "ala de pollo": (203, 24.4, 0.0, 10.4),
    "pavo": (135, 30.0, 0.0, 1.0),
    "carne de ternera": (250, 26.0, 0.0, 15.0),
    "filete de ternera": (190, 26.0, 0.0, 9.0),
    "carne molida": (250, 26.0, 0.0, 15.0),
    "chuletón de cerdo": (280, 24.0, 0.0, 20.0),
    "cerdo": (242, 27.3, 0.0, 14.0),
    "costillas de cerdo": (280, 24.0, 0.0, 20.0),
    "chuleta de cerdo": (280, 24.0, 0.0, 20.0),
    "jamon": (145, 21.0, 1.0, 5.0),
    "jamon serrano": (250, 28.0, 1.0, 14.0),
    "jamon de york": (110, 22.0, 2.0, 3.0),
    "chorizo": (460, 24.0, 3.0, 39.0),
    "salchicha": (290, 12.0, 3.0, 25.0),
    "bacon": (541, 37.0, 1.4, 42.0),
    "panceta": (541, 37.0, 1.4, 42.0),
    "salami": (357, 25.0, 3.0, 28.0),
    "atun": (132, 29.0, 0.0, 1.3),
    "atun en lata": (116, 25.5, 0.0, 1.0),
    "salmon": (208, 20.4, 0.0, 13.4),
    "salmón": (208, 20.4, 0.0, 13.4),
    "bacalao": (82, 17.8, 0.0, 0.7),
    "merluza": (82, 17.8, 0.0, 0.7),
    "gamba": (99, 24.0, 0.2, 0.3),
    "gambas": (99, 24.0, 0.2, 0.3),
    "camaron": (99, 24.0, 0.2, 0.3),
    "camarón": (99, 24.0, 0.2, 0.3),
    "calamar": (92, 15.6, 3.0, 1.4),
    "calamar a la plancha": (92, 15.6, 3.0, 1.4),
    "pulpo": (75, 16.4, 0.0, 0.5),
    "bacón": (541, 37.0, 1.4, 42.0),
    "huevo": (143, 12.6, 0.7, 9.5),
    "huevos": (143, 12.6, 0.7, 9.5),
    "clara de huevo": (52, 10.9, 0.7, 0.2),
    "yema de huevo": (328, 15.9, 3.6, 28.4),
    "tofu": (144, 15.8, 4.3, 8.7),
    "tempeh": (192, 20.3, 7.6, 10.8),
    "lentejas": (116, 9.0, 20.1, 0.4),
    "garbanzos": (164, 8.9, 27.4, 2.6),
    "judias verdes": (31, 1.8, 7.0, 0.2),
    "judías verdes": (31, 1.8, 7.0, 0.2),
    # --- cereals y harinas ----------------------------------------------
    "arroz blanco": (130, 2.7, 28.2, 0.3),
    "arroz": (130, 2.7, 28.2, 0.3),
    "arroz integral": (123, 2.7, 25.6, 1.0),
    "arroz rojo": (123, 2.5, 24.0, 1.0),
    "pasta": (158, 5.8, 30.9, 0.9),
    "espagueti": (158, 5.8, 30.9, 0.9),
    "macarrones": (158, 5.8, 30.9, 0.9),
    "fideos": (138, 4.5, 25.0, 0.6),
    "pan blanco": (265, 9.0, 49.0, 3.2),
    "pan": (265, 9.0, 49.0, 3.2),
    "pan integral": (247, 13.0, 41.0, 3.4),
    "pan de molde": (264, 8.8, 49.0, 3.3),
    "tostada": (300, 9.0, 55.0, 3.5),
    "tostadas": (300, 9.0, 55.0, 3.5),
    "cereales": (379, 8.0, 84.0, 1.3),
    "copos de avena": (379, 13.2, 67.7, 6.5),
    "avena": (389, 16.9, 66.3, 6.9),
    "quinoa": (120, 4.4, 21.3, 1.9),
    "trigo": (339, 11.5, 72.0, 1.5),
    "harina": (364, 10.3, 76.3, 1.0),
    "tortilla": (310, 8.0, 51.0, 8.0),
    "tortilla de trigo": (310, 8.0, 51.0, 8.0),
    "tortilla de maiz": (218, 5.7, 44.6, 2.9),
    "galleta": (480, 6.5, 66.0, 20.0),
    "galletas": (480, 6.5, 66.0, 20.0),
    "donut": (452, 4.9, 51.0, 25.0),
    "dona": (452, 4.9, 51.0, 25.0),
    "chocolatina": (500, 5.0, 60.0, 26.0),
    "cacao": (228, 19.6, 57.9, 13.7),
    "guisantes": (81, 5.4, 14.5, 0.4),
    "guisante": (81, 5.4, 14.5, 0.4),
    "mejillones": (172, 11.9, 7.2, 4.5),
    "mejillon": (172, 11.9, 7.2, 4.5),
    "mussels": (172, 11.9, 7.2, 4.5),
    "mussel": (172, 11.9, 7.2, 4.5),
    "limon": (29, 1.1, 9.3, 0.3),
    "limón": (29, 1.1, 9.3, 0.3),
    "lemon": (29, 1.1, 9.3, 0.3),
    "cebolla morada": (40, 1.1, 9.3, 0.1),
    "pimiento rojo": (31, 1.0, 6.0, 0.3),
    "pimiento verde": (20, 1.0, 4.6, 0.2),
    "champinones": (22, 3.1, 3.3, 0.3),
    "espinacas": (23, 2.9, 3.6, 0.4),
    "perejil": (36, 3.7, 6.3, 0.1),
    "ajo": (149, 6.4, 33.1, 0.5),
    "calabacin": (17, 1.2, 3.1, 0.3),
    "berenjena": (25, 1.0, 6.0, 0.2),
    "coliflor": (25, 1.9, 5.0, 0.3),
    "brócoli": (34, 2.8, 6.6, 0.4),
    "brocoli": (34, 2.8, 6.6, 0.4),
    "judías verdes": (31, 1.8, 7.0, 0.2),
    "judias verdes": (31, 1.8, 7.0, 0.2),
    "champiñon": (22, 3.1, 3.3, 0.3),
    "champinon": (22, 3.1, 3.3, 0.3),
    "pesto": (418, 5.0, 12.0, 40.0),
    "quinoa": (120, 4.4, 21.3, 1.9),
    "tortilla de patata": (310, 5.0, 35.0, 15.0),
    "spring roll": (200, 6.0, 22.0, 9.0),
    "gyoza": (220, 9.0, 25.0, 9.0),
    "empanada": (270, 9.0, 26.0, 14.0),
    "sopa de verduras": (30, 1.2, 5.0, 0.8),
    "crema de calabaza": (45, 1.2, 7.0, 1.8),
    "salmorejo": (60, 2.0, 5.0, 3.5),
    "gazpacho": (25, 1.0, 4.0, 0.5),
    "ratatouille": (60, 1.5, 8.0, 2.5),
    "lasaña de verduras": (120, 5.0, 14.0, 4.5),
    "quiche": (250, 9.0, 18.0, 15.0),
    "crepe": (110, 3.5, 16.0, 3.5),
    "tortilla de crepe": (110, 3.5, 16.0, 3.5),
    "waffle": (291, 7.9, 33.0, 13.0),
    "panqueque": (110, 3.5, 16.0, 3.5),
    "bagel": (250, 10.0, 48.0, 1.5),
    "muesli": (380, 10.0, 65.0, 8.0),
    "granola": (471, 10.0, 64.0, 20.0),
    "chocolate con leche": (535, 7.7, 59.4, 29.7),
    "chocolate negro 70%": (598, 7.8, 45.9, 42.6),
    "bicarbonato": (0, 0.0, 0.0, 0.0),
    "levadura": (325, 40.4, 41.2, 5.6),
    "harina de trigo": (364, 10.3, 76.3, 1.0),
    "aceite de oliva virgen extra": (884, 0.0, 0.0, 100.0),
    "vinagre": (18, 0.0, 0.9, 0.0),
    "sal y pimienta": (0, 0.0, 0.0, 0.0),
    "salsa de tomate": (85, 1.6, 19.0, 0.3),
    "tomate triturado": (41, 1.6, 8.8, 0.3),
    "salsaioli": (15, 0.2, 2.0, 0.8),
    "guacamole": (150, 2.0, 8.5, 12.0),
    "hummus": (166, 7.9, 14.3, 9.6),
    "tahini": (595, 17.0, 21.2, 53.8),
    "salsa de soja": (53, 8.1, 4.9, 0.6),
    "oregano": (265, 9.0, 68.9, 4.3),
    "romero": (131, 3.3, 20.7, 5.9),
    "hierbas frescas": (44, 3.3, 7.0, 0.8),
    # --- Lacteos y huevos ----------------------------------------------
    "leche": (61, 3.2, 4.8, 3.3),
    "leche entera": (61, 3.2, 4.8, 3.3),
    "leche desnatada": (35, 3.4, 5.0, 0.2),
    "leche de avena": (43, 1.0, 6.7, 1.5),
    "leche de almendra": (15, 0.6, 0.6, 1.2),
    "yogur": (72, 3.5, 4.7, 3.9),
    "yogur natural": (72, 3.5, 4.7, 3.9),
    "yogur griego": (97, 9.0, 4.0, 5.0),
    "queso fresco": (84, 11.0, 4.1, 2.3),
    "queso cheddar": (403, 24.9, 1.3, 33.1),
    "queso": (402, 25.0, 3.5, 32.0),
    "mascarpone": (440, 6.0, 3.0, 44.0),
    "mozzarella": (280, 27.5, 3.1, 17.1),
    "parmesano": (392, 35.8, 3.2, 25.8),
    "mantequilla": (717, 0.9, 0.1, 81.1),
    "nata": (340, 2.0, 3.0, 35.0),
    "crema": (340, 2.0, 3.0, 35.0),
    "nata para cocinar": (340, 2.0, 3.0, 35.0),
    # --- verduras -------------------------------------------------------
    "lechuga": (15, 1.4, 2.9, 0.2),
    "tomate": (18, 0.9, 3.9, 0.2),
    "tomates": (18, 0.9, 3.9, 0.2),
    "pepino": (15, 0.7, 3.6, 0.1),
    "zanahoria": (41, 0.9, 9.6, 0.2),
    "pimiento": (31, 1.0, 6.0, 0.3),
    "pimientos": (31, 1.0, 6.0, 0.3),
    "cebolla": (40, 1.1, 9.3, 0.1),
    "ajo": (149, 6.4, 33.1, 0.5),
    "calabacin": (17, 1.2, 3.1, 0.3),
    "calabaza": (26, 1.0, 6.5, 0.1),
    "berenjena": (25, 1.0, 6.0, 0.2),
    "espinacas": (23, 2.9, 3.6, 0.4),
    "brócoli": (34, 2.8, 6.6, 0.4),
    "brocoli": (34, 2.8, 6.6, 0.4),
    "coliflor": (25, 1.9, 5.0, 0.3),
    "col": (25, 1.9, 5.0, 0.3),
    "champinon": (22, 3.1, 3.3, 0.3),
    "champiñon": (22, 3.1, 3.3, 0.3),
    "champiñones": (22, 3.1, 3.3, 0.3),
    "setas": (22, 3.1, 3.3, 0.3),
    "patata": (77, 2.0, 17.5, 0.1),
    "patatas": (77, 2.0, 17.5, 0.1),
    "patata frita": (312, 3.4, 41.4, 15.3),
    "papas fritas": (312, 3.4, 41.4, 15.3),
    "boniato": (86, 1.6, 20.1, 0.1),
    "batata": (86, 1.6, 20.1, 0.1),
    "maiz": (86, 3.3, 19.0, 1.2),
    "maíz": (86, 3.3, 19.0, 1.2),
    "aguacate": (160, 2.0, 8.5, 14.7),
    "plátano": (89, 1.1, 22.8, 0.3),
    "platano": (89, 1.1, 22.8, 0.3),
    "banana": (89, 1.1, 22.8, 0.3),
    "manzana": (52, 0.3, 13.8, 0.2),
    "naranja": (47, 0.9, 11.8, 0.1),
    "uva": (69, 0.7, 18.1, 0.2),
    "fresa": (32, 0.7, 7.7, 0.3),
    "fresas": (32, 0.7, 7.7, 0.3),
    "arandano": (57, 0.7, 14.5, 0.3),
    "arándano": (57, 0.7, 14.5, 0.3),
    "melon": (30, 0.8, 8.1, 0.2),
    "melón": (30, 0.8, 8.1, 0.2),
    "sandia": (30, 0.6, 7.6, 0.2),
    "sandía": (30, 0.6, 7.6, 0.2),
    "piña": (50, 0.5, 13.1, 0.1),
    "pina": (50, 0.5, 13.1, 0.1),
    "mango": (60, 0.8, 15.0, 0.4),
    "miel": (304, 0.3, 82.4, 0.0),
    "azucar": (387, 0.0, 100.0, 0.0),
    "azúcar": (387, 0.0, 100.0, 0.0),
    # ---prepared / Industrial -------------------------------------------
    "pizza": (266, 11.0, 33.0, 10.0),
    "pizza margarita": (266, 11.0, 33.0, 10.0),
    "hamburguesa": (250, 12.0, 20.0, 12.0),
    "hamburguesa de queso": (295, 14.0, 22.0, 16.0),
    "bocadillo": (280, 11.0, 30.0, 11.0),
    "sandia de tortilla": (230, 9.0, 24.0, 10.0),
    "quesadilla": (300, 13.0, 26.0, 16.0),
    "wrap": (300, 12.0, 35.0, 12.0),
    "burrito": (206, 8.0, 26.0, 8.0),
    "taco": (226, 9.0, 20.0, 12.0),
    "tacos": (226, 9.0, 20.0, 12.0),
    "sushi": (145, 6.0, 26.0, 2.0),
    "sushi de salmon": (150, 8.0, 20.0, 4.0),
    "maki": (150, 6.0, 28.0, 2.0),
    "paella": (160, 8.0, 22.0, 5.0),
    "risotto": (145, 4.0, 24.0, 4.0),
    "lasaña": (165, 9.0, 14.0, 8.0),
    "lasana": (165, 9.0, 14.0, 8.0),
    "migas": (290, 8.0, 36.0, 12.0),
    "croqueta": (240, 8.0, 22.0, 12.0),
    "croquetas": (240, 8.0, 22.0, 12.0),
    "empanadilla": (270, 9.0, 26.0, 14.0),
    "fritura": (290, 8.0, 24.0, 18.0),
    "ensalada": (20, 1.2, 3.7, 0.2),
    "ensalada Cesar": (160, 8.0, 6.0, 12.0),
    "gazpacho": (25, 1.0, 4.0, 0.5),
    "crema de verduras": (40, 1.2, 6.0, 1.5),
    "sopa": (35, 1.5, 5.0, 1.0),
    "caldo": (35, 1.5, 5.0, 1.0),
    "puré": (70, 2.0, 13.0, 1.5),
    "pure": (70, 2.0, 13.0, 1.5),
    "tortilla francesa": (180, 10.0, 2.0, 14.0),
    "huevo frito": (190, 13.0, 1.0, 15.0),
    "huevos fritos": (190, 13.0, 1.0, 15.0),
    "tostada con tomate": (110, 4.0, 16.0, 3.5),
    "pan con tomate": (110, 4.0, 16.0, 3.5),
    "pizza carbonara": (280, 11.0, 32.0, 12.0),
    "bistec": (220, 26.0, 0.0, 12.0),
    "carne a la plancha": (220, 26.0, 0.0, 12.0),
    "mariscos": (90, 18.0, 2.0, 1.0),
    "pescado": (110, 20.0, 0.0, 3.0),
    "verduras": (30, 2.0, 5.0, 0.5),
    "fruta": (52, 0.6, 13.0, 0.3),
    # --- grasas y extras -------------------------------------------------
    "aceite de oliva": (884, 0.0, 0.0, 100.0),
    "aceite": (884, 0.0, 0.0, 100.0),
    "olive oil": (884, 0.0, 0.0, 100.0),
    "oil": (884, 0.0, 0.0, 100.0),
    "mantequilla de cacahuate": (588, 25.0, 20.0, 50.0),
    "cacahuete": (567, 25.8, 16.1, 49.2),
    "almendra": (579, 21.2, 21.6, 49.9),
    "nueces": (654, 15.2, 13.7, 65.2),
    "pistacho": (560, 20.2, 27.2, 45.3),
    "chocolate": (546, 4.9, 61.2, 31.3),
    "chocolate negro": (546, 4.9, 61.2, 31.3),
    "leche condensada": (320, 7.0, 55.0, 14.0),
    "sirope": (290, 0.0, 72.0, 0.0),
    "sal": (0, 0.0, 0.0, 0.0),
    "pimienta": (251, 10.4, 64.0, 3.3),
    "especias": (250, 10.0, 50.0, 8.0),
    "perejil": (36, 3.7, 6.3, 0.1),
    "tomate frito": (85, 1.6, 19.0, 0.3),
    "salsa": (60, 1.5, 10.0, 1.5),
    "salsa de soja": (53, 8.1, 4.9, 0.6),
    "mayonesa": (680, 1.0, 1.0, 75.0),
    "ketchup": (101, 1.0, 25.8, 0.1),
    "mostaza": (66, 4.4, 5.8, 3.3),
    "cafe": (1, 0.1, 0.0, 0.0),
    "café": (1, 0.1, 0.0, 0.0),
    "te": (1, 0.0, 0.2, 0.0),
    "té": (1, 0.0, 0.2, 0.0),
    "refresco": (42, 0.0, 10.6, 0.0),
    "cerveza": (43, 0.5, 3.6, 0.0),
    "vino": (85, 0.1, 2.6, 0.0),
    "zumo": (45, 0.4, 11.0, 0.1),
    "zumo de naranja": (45, 0.7, 10.4, 0.2),
    "bebida energetica": (45, 0.3, 11.0, 0.0),
}

# Sinonimos -> clave canonica.
ALIASES: dict[str, str] = {
    # Compuestos en ingles: evitan que el emparejamiento por palabra caiga
    # en el sinonimo equivocado (ej. "bell pepper" -> pimienta negra).
    "bell pepper": "pimiento",
    "bell peppers": "pimiento",
    "red pepper": "pimiento rojo",
    "green pepper": "pimiento verde",
    "green peas": "guisantes",
    "sweet corn": "maiz",
    "corn kernels": "maiz",
    "spring onion": "cebolla",
    "scallion": "cebolla",
    "cherry tomatoes": "tomate",
    "cherry tomato": "tomate",
    "chicken thigh": "muslo de pollo",
    "chicken thighs": "muslo de pollo",
    "beef steak": "bistec",
    "pork chop": "chuleta de cerdo",
    "pork belly": "bacon",
    "baby spinach": "espinacas",
    "extra virgin olive oil": "aceite de oliva virgen extra",
    "sunflower oil": "aceite",
    "vegetable oil": "aceite",
    "sesame oil": "aceite",
    "mashed potatoes": "pure",
    "black pepper": "pimienta",
    "chicken breast": "pechuga de pollo",
    "chicken": "pollo",
    "beef": "carne de ternera",
    "steak": "bistec",
    "pork": "cerdo",
    "turkey": "pavo",
    "egg": "huevo",
    "eggs": "huevos",
    "rice": "arroz blanco",
    "bread": "pan blanco",
    "whole wheat bread": "pan integral",
    "pasta": "pasta",
    "noodles": "fideos",
    "milk": "leche",
    "yogurt": "yogur",
    "yoghurt": "yogur",
    "cheese": "queso",
    "butter": "mantequilla",
    "cream": "nata",
    "olive oil": "aceite de oliva",
    "oil": "aceite",
    "sugar": "azucar",
    "salt": "sal",
    "pepper": "pimienta",
    "tomatoes": "tomate",
    "onion": "cebolla",
    "garlic": "ajo",
    "potato": "patata",
    "potatoes": "patatas",
    "french fries": "papas fritas",
    "salad": "ensalada",
    "soup": "sopa",
    "chicken wings": "ala de pollo",
    "fries": "papas fritas",
    "steak": "bistec",
    "hamburger": "hamburguesa",
    "oats": "copos de avena",
    "oatmeal": "copos de avena",
    "banana": "platano",
    "apple": "manzana",
    "orange juice": "zumo de naranja",
    "orange": "naranja",
    "olive": "aceite de oliva",
    "avocado": "aguacate",
    "hummus": "puré",
    "toast": "tostada",
    "fried egg": "huevo frito",
    "omelette": "tortilla francesa",
    "omelet": "tortilla francesa",
    "burger": "hamburguesa",
    "chips": "papas fritas",
    "cake": "galleta",
    "cookie": "galleta",
    "cookies": "galletas",
    "beer": "cerveza",
    "wine": "vino",
    "juice": "zumo",
    "coffee": "cafe",
    "tea": "te",
    "chicken nuggets": "fritura",
    "mayonnaise": "mayonesa",
    "soy sauce": "salsa de soja",
    "bacon": "bacon",
    "sausage": "salchicha",
    "pork ribs": "costillas de cerdo",
    "tuna": "atun",
    "salmon": "salmon",
    "shrimp": "gamba",
    "prawns": "gamba",
    "squid": "calamar",
    "cod": "bacalao",
    "hake": "merluza",
    "lentils": "lentejas",
    "chickpeas": "garbanzos",
    "tofu": "tofu",
    "quinoa": "quinoa",
    "corn": "maiz",
    "corn tortilla": "tortilla de maiz",
    "flour tortilla": "tortilla de trigo",
    "spaghetti": "espagueti",
    "macaroni": "macarrones",
    "hummus": "pure",
}

# Unidades -> gramos (aprox, para platos caseros).
UNIT_TO_GRAMS: dict[str, float] = {
    "g": 1.0,
    "gr": 1.0,
    "g.": 1.0,
    "gramo": 1.0,
    "gramos": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "kg": 1000.0,
    "kilo": 1000.0,
    "kilos": 1000.0,
    "kilogram": 1000.0,
    "kilograms": 1000.0,
    "ml": 1.0,
    "milliliter": 1.0,
    "milliliters": 1.0,
    "millilitre": 1.0,
    "millilitres": 1.0,
    "cl": 10.0,
    "l": 1000.0,
    "litro": 1000.0,
    "litros": 1000.0,
    "liter": 1000.0,
    "liters": 1000.0,
    "cup": 240.0,
    "cups": 240.0,
    "taza": 240.0,
    "tazas": 240.0,
    "tbsp": 15.0,
    "tbs": 15.0,
    "tablespoon": 15.0,
    "tablespoons": 15.0,
    "cda": 15.0,
    "cucharada": 15.0,
    "cucharadas": 15.0,
    "tsp": 5.0,
    "cdta": 5.0,
    "cucharadita": 5.0,
    "cucharaditas": 5.0,
    "teaspoon": 5.0,
    "teaspoons": 5.0,
    "oz": 28.35,
    "ounce": 28.35,
    "ounces": 28.35,
    "onza": 28.35,
    "onzas": 28.35,
    "lb": 453.6,
    "pound": 453.6,
    "pounds": 453.6,
    "libra": 453.6,
    "libras": 453.6,
    "slice": 30.0,
    "slices": 30.0,
    "rebanada": 30.0,
    "rebanadas": 30.0,
    "piece": 90.0,
    "pieces": 90.0,
    "pieza": 90.0,
    "piezas": 90.0,
    "unit": 60.0,
    "units": 60.0,
    "unidad": 60.0,
    "unidades": 60.0,
    "handful": 50.0,
    "handfuls": 50.0,
    "puñado": 50.0,
    "puñados": 50.0,
    "bowl": 250.0,
    "bowls": 250.0,
    "tazon": 250.0,
    "plato": 300.0,
    "plate": 300.0,
    "glass": 250.0,
    "bottle": 500.0,
    "botella": 500.0,
    "can": 330.0,
    "cans": 330.0,
    "lata": 330.0,
    "bar": 50.0,
    "barra": 50.0,
    "serving": 200.0,
    "servings": 200.0,
    "racion": 200.0,
    "ración": 200.0,
    "portion": 200.0,
    "portions": 200.0,
    "fist": 180.0,
    "puno": 180.0,
    "palm": 100.0,
    "palma": 100.0,
    "drizzle": 5.0,
    "sprinkle": 2.0,
    "pinch": 1.0,
    "pizca": 1.0,
}

# Unidades de masa: si el modelo escribe "grams" y no esta en la tabla, el
# numero que acompaña es directamente el peso en gramos.
MASS_UNITS = {
    "g",
    "gr",
    "g.",
    "gram",
    "grams",
    "gramo",
    "gramos",
    "kg",
    "kilo",
    "kilos",
    "kilogram",
    "kilograms",
    "oz",
    "ounce",
    "ounces",
    "onza",
    "onzas",
    "lb",
    "pound",
    "pounds",
    "libra",
    "libras",
}

# Equivalencias "una porción": pesos medios de un racion tipico por ingrediente.
DEFAULT_SERVING_GRAMS = 100.0

# Gramos aproximados por pieza para alimentos con porcion muy variable.
PIECE_GRAMS: dict[str, float] = {
    "huevo": 50.0,
    "huevos": 50.0,
    "tortilla": 45.0,
    "galleta": 10.0,
    "galletas": 10.0,
    "dona": 60.0,
    "donut": 60.0,
    "chocolatina": 20.0,
    "platano": 120.0,
    "manzana": 180.0,
    "naranja": 150.0,
    "aguacate": 150.0,
    "tomate": 120.0,
    "zanahoria": 80.0,
    "pepino": 150.0,
    "cebolla": 110.0,
    "ajo": 5.0,
    "patata": 150.0,
    "patatas": 150.0,
    "papas fritas": 100.0,
    "chuleta de cerdo": 200.0,
    "bistec": 200.0,
    "filete de ternera": 200.0,
    "pechuga de pollo": 150.0,
    "muslo de pollo": 120.0,
    "ala de pollo": 45.0,
    "salmón": 150.0,
    "salmon": 150.0,
    "filete de salmon": 150.0,
    "filete de salmón": 150.0,
    "gamba": 15.0,
    "gambas": 15.0,
    "calamar": 100.0,
    "pizza": 120.0,
    "taco": 90.0,
    "tacos": 90.0,
    "hamburguesa": 220.0,
    "croqueta": 40.0,
    "croquetas": 40.0,
    "galleta": 10.0,
    "vaina": 120.0,
    "zanahoria baby": 15.0,
}


def normalize(text: str) -> str:
    """Minusculas, sin acentos ni caracteres raros, para comparar nombres."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_lookup() -> dict[str, tuple[str, tuple[float, float, float, float]]]:
    """alias normalizado -> (clave canonica de la tabla, valores por 100 g)."""
    lookup: dict[str, tuple[str, tuple[float, float, float, float]]] = {}
    for name, values in INGREDIENTS.items():
        lookup[normalize(name)] = (name, values)
    for alias, canonical in ALIASES.items():
        if canonical in INGREDIENTS:
            lookup[normalize(alias)] = (canonical, INGREDIENTS[canonical])
    return lookup


LOOKUP = _build_lookup()

# Palabras que describen la coccion/preparacion y no son ingredientes.
PREPARATION_WORDS = {
    "cocido", "cocida", "asado", "asada", "grillado", "frito", "frita", "a la plancha",
    "plancha", "al horno", "horneado", "horneada", "hervido", "hervida", "crudo", "cruda",
    "en salsa", "tomate", "con salsa", "salteado", "salteada", "empanado", "rebozado",
    "sin piel", "con piel", "troceado", "picado", "entero", "fresco", "fresca", "seco",
    "cocida al vapor", "al vapor", "en aceite", "en vino", "con arroz", "y", "de",
    "a", "al", "en", "con", "sin",
}


@dataclass
class IngredientMatch:
    canonical: str
    grams: float
    kcal: float
    protein: float
    carbs: float
    fat: float
    matched: bool


def _strip_preparation(name: str) -> str:
    tokens = normalize(name).split()
    kept = [t for t in tokens if t not in PREPARATION_WORDS]
    return " ".join(kept) if kept else normalize(name)


def _lookup_ingredient(
    name: str,
) -> tuple[str, tuple[float, float, float, float]] | None:
    key = normalize(name)
    if not key:
        return None
    if key in LOOKUP:
        return LOOKUP[key]

    stripped = _strip_preparation(name)
    if stripped in LOOKUP:
        return LOOKUP[stripped]

    # Coincidencia por token mas largo contenido en la clave.
    candidates = [k for k in LOOKUP if k and (k in stripped or stripped in k)]
    if candidates:
        best = max(candidates, key=len)
        return LOOKUP[best]

    # Coincidencia por palabra suelta (la mas especifica).
    words = [w for w in stripped.split() if len(w) > 3]
    for word in sorted(words, key=len, reverse=True):
        for key, entry in LOOKUP.items():
            if key == word or word in key.split():
                return entry
    return None


def match_ingredient(
    name: str,
    quantity: float | None = None,
    unit: str | None = None,
    default_grams: float | None = None,
) -> IngredientMatch:
    """Resuelve un ingrediente del modelo a gramos y macros.

    `quantity`/`unit` provienen del texto del modelo ("150g of chicken").
    Si no hay cantidad, se usa una porcion por defecto segun el alimento.
    """
    found = _lookup_ingredient(name)
    canonical = found[0] if found else normalize(name)
    per100 = found[1] if found else (0.0, 0.0, 0.0, 0.0)

    grams: float | None = None
    unit_key = normalize(unit) if unit else ""
    factor = UNIT_TO_GRAMS.get(unit_key)

    if quantity and quantity > 0:
        if factor is not None:
            # Unidad conocida: "150g", "2 slices", "1 cup"...
            grams = quantity * factor
        elif unit_key in MASS_UNITS:
            # Unidad de masa en ingles que no estaba en la tabla.
            grams = quantity
        elif not unit_key:
            # Sin unidad: si el alimento tiene peso por pieza conocido
            # ("2 eggs" -> 2 x 50 g) se usa; si no, el numero son gramos.
            if canonical in PIECE_GRAMS:
                grams = PIECE_GRAMS[canonical] * quantity
            else:
                grams = quantity

    if grams is None:
        key = canonical
        if key in PIECE_GRAMS:
            grams = PIECE_GRAMS[key] * (quantity or 1)
        elif default_grams:
            grams = default_grams
        else:
            grams = DEFAULT_SERVING_GRAMS

    factor = grams / 100.0
    return IngredientMatch(
        canonical=canonical,
        grams=round(grams, 1),
        kcal=round(per100[0] * factor, 1),
        protein=round(per100[1] * factor, 2),
        carbs=round(per100[2] * factor, 2),
        fat=round(per100[3] * factor, 2),
        matched=found is not None,
    )


def known_ingredients() -> Iterable[str]:
    return INGREDIENTS.keys()
