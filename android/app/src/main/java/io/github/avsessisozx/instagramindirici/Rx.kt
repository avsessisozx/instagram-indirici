package io.github.avsessisozx.instagramindirici

import java.util.regex.Pattern

/**
 * Python'daki gibi Unicode farkında düzenli ifade (\s, \d, \S tüm dillerdeki karakterleri kapsar).
 * Android'in düzenli ifade motoru bunu zaten varsayılan olarak yapar ve (?U) bayrağını kabul etmez;
 * bilgisayardaki Java ise bayrağı ister (birim testleri orada çalışır).
 */
object Rx {
    private val isAndroid: Boolean = System.getProperty("java.vendor")?.contains("Android", ignoreCase = true) == true ||
        System.getProperty("java.vm.name")?.contains("Dalvik", ignoreCase = true) == true

    fun unicode(pattern: String, ignoreCase: Boolean = false): Regex {
        var flags = if (ignoreCase) Pattern.CASE_INSENSITIVE or Pattern.UNICODE_CASE else 0
        if (!isAndroid) flags = flags or Pattern.UNICODE_CHARACTER_CLASS
        return Pattern.compile(pattern, flags).toRegex()
    }
}
