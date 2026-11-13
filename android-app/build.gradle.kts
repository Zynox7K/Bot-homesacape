// Versions du toolchain. Kotlin 1.9.24 <-> Compose Compiler 1.5.14 : c'est la
// correspondance officielle, ne pas décaler l'un sans l'autre.
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}
