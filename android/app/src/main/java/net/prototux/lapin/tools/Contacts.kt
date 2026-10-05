package net.prototux.lapin.tools

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.ContactsContract.CommonDataKinds.Phone

/** The phone's address book: people with phone numbers, matched fuzzily. */
class Contacts(private val context: Context) {
    data class Number(val number: String, val type: String, val primary: Boolean)
    data class Person(val id: Long, val name: String, val numbers: List<Number>) {
        /** The number to call/text: primary, else mobile, else the first. */
        val best: Number get() = numbers.firstOrNull { it.primary } ?: numbers.firstOrNull { it.type == "mobile" } ?: numbers.first()
    }

    sealed interface Resolved {
        data class Ok(val name: String, val number: String) : Resolved
        data class Err(val error: String, val candidates: List<String> = emptyList()) : Resolved
    }

    fun granted() = context.checkSelfPermission(Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED

    fun all(): List<Person> {
        val byId = LinkedHashMap<Long, MutableList<Pair<String, Number>>>()
        context.contentResolver.query(
            Phone.CONTENT_URI,
            arrayOf(Phone.CONTACT_ID, Phone.DISPLAY_NAME, Phone.NUMBER, Phone.TYPE, Phone.IS_SUPER_PRIMARY),
            null, null, null,
        )?.use { c ->
            while (c.moveToNext()) {
                val id = c.getLong(0)
                val name = c.getString(1) ?: continue
                val num = c.getString(2) ?: continue
                val type = when (c.getInt(3)) {
                    Phone.TYPE_MOBILE -> "mobile"
                    Phone.TYPE_HOME -> "home"
                    Phone.TYPE_WORK, Phone.TYPE_WORK_MOBILE -> "work"
                    else -> "other"
                }
                byId.getOrPut(id) { mutableListOf() } += name to Number(num, type, c.getInt(4) != 0)
            }
        }
        return byId.map { (id, l) ->
            Person(id, l.first().first, l.map { it.second }.distinctBy { digits(it.number) })
        }
    }

    fun find(name: String): List<Fuzzy.Match<Person>> = Fuzzy.rank(name, all(), min = 0.72) { listOf(it.name) }

    /** A contact name or a number -> the number to use. */
    fun resolve(contact: String): Resolved {
        val c = contact.trim()
        if (c.isEmpty()) return Resolved.Err("no contact given")
        if (looksLikeNumber(c)) return Resolved.Ok(c, c.filter { it.isDigit() || it == '+' })
        if (!granted()) return Resolved.Err("permission to read contacts not granted; ask the user to open the Lapin app and allow it")
        val ranked = find(c)
        if (ranked.isEmpty()) return Resolved.Err("no contact named $c")
        val p = Fuzzy.pick(ranked) { a, b -> a.id == b.id || Fuzzy.normalize(a.name) == Fuzzy.normalize(b.name) }
            ?: return Resolved.Err("several contacts match $c; ask which one", ranked.take(5).map { it.item.name }.distinct())
        return Resolved.Ok(p.name, p.best.number)
    }

    companion object {
        fun digits(s: String) = s.filter { it.isDigit() }
        fun looksLikeNumber(s: String) = s.all { it.isDigit() || it in "+ ()-." } && digits(s).length >= 3
    }
}
