package io.github.avsessisozx.instagramindirici

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Mp4Mux, tools/mp4mux_ref.py ile BİREBİR aynı baytları üretmeli. Beklenen dosyalar o referanstan çıktı ve
 * ffmpeg ile çözülüp kaynakla karşılaştırılarak doğrulandı (tools/mp4mux_ref.py başlığına bak).
 */
class Mp4MuxTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun res(name: String): File {
        val f = tmp.newFile(name)
        val stream = javaClass.classLoader!!.getResourceAsStream("mp4mux/$name") ?: error("missing resource $name")
        stream.use { i -> f.outputStream().use { o -> i.copyTo(o) } }
        return f
    }

    private fun expected(name: String): ByteArray = res(name).readBytes()

    @Test
    fun vp9AndAacMatchReferenceByteForByte() {
        val out = File(tmp.root, "out1.mp4")
        assertNull(Mp4Mux.mux(res("vp9.mp4"), res("aac.mp4"), out, AtomicBoolean(false)))
        assertArrayEquals(expected("expected_vp9_aac.mp4"), out.readBytes())
    }

    @Test
    fun videoOnlyWithBFramesMatchesReference() {
        val out = File(tmp.root, "out2.mp4")
        assertNull(Mp4Mux.mux(res("h264b.mp4"), null, out, AtomicBoolean(false)))
        assertArrayEquals(expected("expected_h264b_video_only.mp4"), out.readBytes())
    }

    @Test
    fun notFragmentedOrGarbageReturnsProblemAndNoFile() {
        val junk = tmp.newFile("junk.mp4").also { it.writeBytes(ByteArray(100) { 7 }) }
        val out = File(tmp.root, "out3.mp4")
        val problem = Mp4Mux.mux(junk, null, out, AtomicBoolean(false))
        assertNotNull(problem)
        assertTrue(!out.exists())
    }
}
