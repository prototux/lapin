package net.prototux.lapin

import net.prototux.lapin.tools.Fuzzy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FuzzyTest {
    private data class C(val id: Int, val name: String)

    private val people = listOf(
        C(1, "Marie Dupont"), C(2, "Marie Leroy"), C(3, "André Martin"), C(4, "Mathilde Roux"),
        C(5, "Jean Dupont"), C(6, "Jeanne Petit"), C(7, "Maman"), C(8, "Hélène Lefèvre"),
    )

    private fun pick(q: String) = Fuzzy.pick(Fuzzy.rank(q, people, 0.72) { listOf(it.name) })?.name

    @Test fun normalize() {
        assertEquals("helene lefevre", Fuzzy.normalize("Hélène  Lefèvre"))
        assertEquals("l appli oeuvre", Fuzzy.normalize("L'appli Œuvre!"))
    }

    @Test fun accentsAndCase() {
        assertEquals("André Martin", pick("andre"))
        assertEquals("Hélène Lefèvre", pick("helene"))
        assertEquals("Hélène Lefèvre", pick("HELENE LEFEVRE"))
    }

    @Test fun typos() {
        assertEquals("Mathilde Roux", pick("matilde"))
        assertEquals("Maman", pick("maman"))
    }

    @Test fun ambiguous() {
        assertNull(pick("marie"))
        val ranked = Fuzzy.rank("marie", people, 0.72) { listOf(it.name) }.map { it.item.name }
        assertTrue(ranked.containsAll(listOf("Marie Dupont", "Marie Leroy")))
    }

    @Test fun exactWordBeatsPrefix() {
        assertEquals("Jean Dupont", pick("jean"))
        assertEquals("Marie Dupont", pick("marie dupont"))
    }

    @Test fun noMatch() {
        assertTrue(Fuzzy.rank("zoé", people, 0.72) { listOf(it.name) }.isEmpty())
    }

    @Test fun sameContactTwiceIsNotAmbiguous() {
        val dup = listOf(C(1, "Paul Bernard"), C(1, "Paul Bernard"))
        val r = Fuzzy.rank("paul", dup, 0.72) { listOf(it.name) }
        assertEquals("Paul Bernard", Fuzzy.pick(r) { a, b -> a.id == b.id }?.name)
    }

    private val apps = listOf("Settings", "Camera", "YouTube", "YouTube Music", "Maps", "Spotify", "Clock", "Chrome", "Photos", "WhatsApp")
    private fun app(q: String): String? {
        val variants = Fuzzy.appVariants(q)
        val ranked = apps.map { a -> Fuzzy.Match(a, variants.maxOf { v -> Fuzzy.score(v, a) - if (v == q) 0.0 else 0.02 }) }
            .filter { it.score >= 0.72 }.sortedByDescending { it.score }
        return Fuzzy.pick(ranked, 0.03)
    }

    @Test fun appsFrenchNames() {
        assertEquals("Settings", app("paramètres"))
        assertEquals("Settings", app("les réglages"))
        assertEquals("Camera", app("appareil photo"))
        assertEquals("Clock", app("horloge"))
        assertEquals("Maps", app("Google Maps"))
        assertEquals("Chrome", app("navigateur"))
    }

    @Test fun appsFillersAndVariants() {
        assertEquals("Spotify", app("l'appli Spotify"))
        assertEquals("YouTube", app("youtube"))
        assertEquals("YouTube Music", app("youtube music"))
        assertEquals("WhatsApp", app("whats app"))
        assertEquals("Spotify", app("spotfy"))
    }

    @Test fun levenshtein() {
        assertEquals(3, Fuzzy.levenshtein("kitten", "sitting"))
        assertEquals(0, Fuzzy.levenshtein("", ""))
    }
}
