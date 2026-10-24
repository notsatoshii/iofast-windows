package com.iofast.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.iofast.android.data.AppDatabase
import com.iofast.android.data.CapturedLink
import com.iofast.android.data.SendState
import com.iofast.android.databinding.ActivityQueueBinding
import kotlinx.coroutines.launch

/**
 * Lists captured links with copy / share / delete actions.
 * Observes Room via Flow; auto-updates on insert/delete.
 */
class QueueActivity : AppCompatActivity() {
 private lateinit var binding: ActivityQueueBinding
 private lateinit var adapter: LinkAdapter

 override fun onCreate(savedInstanceState: Bundle?) {
 super.onCreate(savedInstanceState)
 binding = ActivityQueueBinding.inflate(layoutInflater)
 setContentView(binding.root)

 adapter = LinkAdapter(
 onCopy = { link -> copyToClipboard(link.url) },
 onShare = { link -> shareLink(link.url) },
 onDelete = { link -> deleteLink(link) }
 )
 binding.recyclerView.layoutManager = LinearLayoutManager(this)
 binding.recyclerView.adapter = adapter

 lifecycleScope.launch {
 repeatOnLifecycle(Lifecycle.State.STARTED) {
 AppDatabase.get(this@QueueActivity)
 .capturedLinkDao()
 .observeAll()
 .collect { links ->
 adapter.submitList(links)
 binding.emptyView.visibility = if (links.isEmpty()) View.VISIBLE else View.GONE
 binding.recyclerView.visibility = if (links.isEmpty()) View.GONE else View.VISIBLE
 }
 }
 }
 }

 private fun copyToClipboard(url: String) {
 val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
 cm.setPrimaryClip(ClipData.newPlainText("url", url))
 Toast.makeText(this, "Copied", Toast.LENGTH_SHORT).show()
 }

 private fun shareLink(url: String) {
 startActivity(Intent.createChooser(
 Intent(Intent.ACTION_SEND).apply {
 type = "text/plain"
 putExtra(Intent.EXTRA_TEXT, url)
 },
 "Share link"
 ))
 }

 private fun deleteLink(link: CapturedLink) {
 lifecycleScope.launch {
 AppDatabase.get(this@QueueActivity).capturedLinkDao().delete(link)
 }
 }
}

class LinkAdapter(
 private val onCopy: (CapturedLink) -> Unit,
 private val onShare: (CapturedLink) -> Unit,
 private val onDelete: (CapturedLink) -> Unit
) : RecyclerView.Adapter<LinkAdapter.VH>() {

 private var items = emptyList<CapturedLink>()

 fun submitList(list: List<CapturedLink>) {
 items = list
 notifyDataSetChanged()
 }

 inner class VH(view: View) : RecyclerView.ViewHolder(view) {
 val urlText: TextView = view.findViewById(android.R.id.text1)
 val sourceText: TextView = view.findViewById(android.R.id.text2)
 val btnCopy: View = view.findViewById(R.id.btn_copy)
 val btnShare: View = view.findViewById(R.id.btn_share)
 val btnDelete: View = view.findViewById(R.id.btn_delete)
 }

 override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
 val view = LayoutInflater.from(parent.context)
 .inflate(R.layout.item_link, parent, false)
 return VH(view)
 }

 override fun onBindViewHolder(holder: VH, position: Int) {
 val link = items[position]
 holder.urlText.text = link.url
 val ctx = holder.itemView.context
 val state = when (link.status) {
 SendState.SENT -> ctx.getString(R.string.state_sent)
 SendState.FAILED -> ctx.getString(R.string.state_failed, link.lastError ?: "?")
 else -> link.lastError?.let { ctx.getString(R.string.state_waiting_why, it) }
 ?: ctx.getString(R.string.state_waiting)
 }
 holder.sourceText.text = "${link.sourcePackage ?: "unknown"} · $state"
 holder.btnCopy.setOnClickListener { onCopy(link) }
 holder.btnShare.setOnClickListener { onShare(link) }
 holder.btnDelete.setOnClickListener { onDelete(link) }
 }

 override fun getItemCount() = items.size
}