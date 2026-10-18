package io.github.avsessisozx.instagramindirici

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat

/** Ekran kapansa da indirme sürsün diye ön plan servisi; bildirimde ilerlemeyi gösterir. */
class DownloadService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private var wake: PowerManager.WakeLock? = null
    private var wifi: WifiManager.WifiLock? = null
    private var ticking = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Core.init(this)
        createChannel()
        val type = if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0
        try {
            ServiceCompat.startForeground(this, ONGOING_ID, build(ongoing = true), type)
        } catch (e: Exception) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (wake == null) {
            wake = (getSystemService(Context.POWER_SERVICE) as PowerManager)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "InstagramIndirici:download").apply {
                    setReferenceCounted(false); acquire(6 * 60 * 60 * 1000L)
                }
        }
        if (wifi == null) {
            @Suppress("DEPRECATION")
            wifi = (applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager)
                .createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "InstagramIndirici:download").apply {
                    setReferenceCounted(false); acquire()
                }
        }
        if (!ticking) { ticking = true; handler.post(tick) }
        return START_NOT_STICKY
    }

    private val tick = object : Runnable {
        override fun run() {
            val nm = getSystemService(NotificationManager::class.java)
            if (Core.engine.running) {
                nm.notify(ONGOING_ID, build(ongoing = true))
                handler.postDelayed(this, 1000)
            } else {
                ticking = false
                ServiceCompat.stopForeground(this@DownloadService, ServiceCompat.STOP_FOREGROUND_REMOVE)
                if (Core.engine.mode != Engine.MODE_LOGIN) nm.notify(DONE_ID, build(ongoing = false))
                stopSelf()
            }
        }
    }

    private fun createChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, Core.text("android.notif_channel"), NotificationManager.IMPORTANCE_LOW))
    }

    private fun build(ongoing: Boolean): Notification {
        val e = Core.engine
        val open = PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val b = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_download)
            .setContentTitle(Core.text("android.notif_title"))
            .setContentIntent(open)
            .setOnlyAlertOnce(true)
            .setSilent(true)
        val total = e.filesTotal.get()
        val finished = e.filesDone.get() + e.filesSkipped.get() + e.filesFailed.get()
        if (ongoing) {
            b.setOngoing(true)
            when {
                e.verify -> b.setContentText(Core.text("android.notif_verify")).setSilent(false)
                e.collecting -> b.setContentText(Core.text("android.notif_collecting", "n" to e.foundPosts.get())).setProgress(0, 0, true)
                else -> b.setContentText(Core.text("android.notif_progress", "done" to finished, "total" to total))
                    .setProgress(total.coerceAtLeast(1), finished, total == 0)
            }
        } else {
            b.setAutoCancel(true)
            b.setContentText(when (e.phase) {
                "done" -> Core.text("android.notif_done", "n" to e.filesDone.get())
                "stopped" -> Core.text("android.notif_stopped")
                else -> Core.text("android.notif_failed")
            })
        }
        return b.build()
    }

    override fun onDestroy() {
        handler.removeCallbacks(tick)
        try { wake?.release() } catch (e: Exception) {}
        try { wifi?.release() } catch (e: Exception) {}
        super.onDestroy()
    }

    companion object {
        const val CHANNEL = "indirmeler"
        const val ONGOING_ID = 1
        const val DONE_ID = 2
    }
}
