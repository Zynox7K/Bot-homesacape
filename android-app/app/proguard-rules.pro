# Aucune règle spécifique : isMinifyEnabled est désactivé sur les deux variantes,
# la réflexion utilisée ici (org.json, HttpURLConnection) n'est pas obfusquée.
# Si vous activez R8 sur une build de publication, conservez ces règles :
-keep class dev.zynox.autoplay.TouchAccessibilityService { *; }
-keep class dev.zynox.autoplay.FloatingOverlayService { *; }
