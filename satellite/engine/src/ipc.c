#define _GNU_SOURCE
#include "ipc.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include "common.h"

#define MAX_CLIENTS 6
#define MAX_MSG     (4u * 1024 * 1024)
#define MAX_OUTBUF  (8u * 1024 * 1024)

typedef struct {
    int fd;
    int sub;
    uint8_t *in;
    size_t in_len, in_cap;
    uint8_t *out;
    size_t out_len, out_cap;
    int dropped;
} client_t;

typedef struct msg {
    struct msg *next;
    int client, sub;
    size_t len;
    uint8_t data[];         /* framed */
} msg_t;

static client_t clients[MAX_CLIENTS];
static int listen_fd = -1;
static int wake_pipe[2] = {-1, -1};
static ipc_handler_t on_msg;
static char sock_path[108];

static pthread_mutex_t qlock = PTHREAD_MUTEX_INITIALIZER;
static msg_t *qhead, *qtail;
static size_t qbytes;

int ipc_listen(const char *path, ipc_handler_t handler)
{
    on_msg = handler;
    for (int i = 0; i < MAX_CLIENTS; i++)
        clients[i].fd = -1;
    if (pipe(wake_pipe) < 0)
        return -1;
    fcntl(wake_pipe[0], F_SETFL, O_NONBLOCK);
    fcntl(wake_pipe[1], F_SETFL, O_NONBLOCK);

    listen_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (listen_fd < 0)
        return -1;
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path);
    snprintf(sock_path, sizeof sock_path, "%s", path);
    unlink(path);
    if (bind(listen_fd, (struct sockaddr *)&a, sizeof a) < 0 || listen(listen_fd, 4) < 0) {
        LOGE("ipc: cannot listen on %s: %s", path, strerror(errno));
        return -1;
    }
    chmod(path, 0660);
    fcntl(listen_fd, F_SETFL, O_NONBLOCK);
    return 0;
}

void ipc_close(void)
{
    for (int i = 0; i < MAX_CLIENTS; i++)
        if (clients[i].fd >= 0)
            close(clients[i].fd);
    if (listen_fd >= 0) {
        close(listen_fd);
        unlink(sock_path);
    }
}

int ipc_clients(void)
{
    int n = 0;
    for (int i = 0; i < MAX_CLIENTS; i++)
        n += clients[i].fd >= 0;
    return n;
}

void ipc_subscribe(int client, int mask)
{
    if (client >= 0 && client < MAX_CLIENTS)
        clients[client].sub = mask;
}

static void drop_client(int i)
{
    client_t *c = &clients[i];
    close(c->fd);
    free(c->in);
    free(c->out);
    memset(c, 0, sizeof *c);
    c->fd = -1;
    LOGI("ipc: client %d disconnected", i);
}

void ipc_send(int client, int type, int sub, const void *data, size_t len)
{
    msg_t *m = malloc(sizeof(msg_t) + len + 5);
    if (!m)
        return;
    uint32_t l = (uint32_t)(len + 1);
    memcpy(m->data, &l, 4);
    m->data[4] = (uint8_t)type;
    memcpy(m->data + 5, data, len);
    m->len = len + 5;
    m->client = client;
    m->sub = sub;
    m->next = NULL;
    pthread_mutex_lock(&qlock);
    if (qbytes > 16u * 1024 * 1024 && sub == SUB_AUDIO) {   /* main loop stalled */
        pthread_mutex_unlock(&qlock);
        free(m);
        return;
    }
    if (qtail)
        qtail->next = m;
    else
        qhead = m;
    qtail = m;
    qbytes += m->len;
    pthread_mutex_unlock(&qlock);
    char b = 1;
    if (write(wake_pipe[1], &b, 1) < 0) {
        /* pipe full: the main loop is already awake */
    }
}

void ipc_json(int client, int sub, const char *fmt, ...)
{
    char buf[16384];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (n < 0)
        return;
    if (n >= (int)sizeof buf)
        n = sizeof buf - 1;
    ipc_send(client, MSG_JSON, sub, buf, (size_t)n);
}

static void append_out(client_t *c, const uint8_t *d, size_t n, int droppable)
{
    if (c->out_len + n > MAX_OUTBUF) {
        if (droppable) {
            c->dropped++;
            return;
        }
    }
    if (c->out_len + n > c->out_cap) {
        size_t cap = c->out_cap ? c->out_cap : 65536;
        while (cap < c->out_len + n)
            cap *= 2;
        uint8_t *p = realloc(c->out, cap);
        if (!p)
            return;
        c->out = p;
        c->out_cap = cap;
    }
    memcpy(c->out + c->out_len, d, n);
    c->out_len += n;
}

static void dispatch_queue(void)
{
    pthread_mutex_lock(&qlock);
    msg_t *m = qhead;
    qhead = qtail = NULL;
    qbytes = 0;
    pthread_mutex_unlock(&qlock);
    while (m) {
        msg_t *n = m->next;
        for (int i = 0; i < MAX_CLIENTS; i++) {
            client_t *c = &clients[i];
            if (c->fd < 0 || (m->client >= 0 && m->client != i))
                continue;
            if (m->sub && !(c->sub & m->sub))
                continue;
            append_out(c, m->data, m->len, m->sub != 0);
        }
        free(m);
        m = n;
    }
}

static void handle_input(int i)
{
    client_t *c = &clients[i];
    for (;;) {
        if (c->in_cap - c->in_len < 65536) {
            size_t cap = c->in_cap ? c->in_cap * 2 : 131072;
            if (cap > MAX_MSG + 65536) {
                drop_client(i);
                return;
            }
            uint8_t *p = realloc(c->in, cap);
            if (!p) {
                drop_client(i);
                return;
            }
            c->in = p;
            c->in_cap = cap;
        }
        ssize_t r = read(c->fd, c->in + c->in_len, c->in_cap - c->in_len);
        if (r == 0) {
            drop_client(i);
            return;
        }
        if (r < 0) {
            if (errno == EAGAIN || errno == EINTR)
                break;
            drop_client(i);
            return;
        }
        c->in_len += (size_t)r;
    }
    size_t off = 0;
    while (c->in_len - off >= 5) {
        uint32_t l;
        memcpy(&l, c->in + off, 4);
        if (l == 0 || l > MAX_MSG) {
            drop_client(i);
            return;
        }
        if (c->in_len - off < 4 + (size_t)l)
            break;
        int type = c->in[off + 4];
        uint8_t *payload = c->in + off + 5;
        size_t plen = l - 1;
        if (type == MSG_JSON) {
            char *js = malloc(plen + 1);        /* NUL-terminated copy */
            if (js) {
                memcpy(js, payload, plen);
                js[plen] = 0;
                on_msg(i, type, (const uint8_t *)js, plen);
                free(js);
            }
        } else {
            on_msg(i, type, payload, plen);
        }
        if (clients[i].fd < 0)
            return;
        off += 4 + l;
    }
    if (off) {
        memmove(c->in, c->in + off, c->in_len - off);
        c->in_len -= off;
    }
}

void ipc_poll(int timeout_ms)
{
    struct pollfd p[MAX_CLIENTS + 2];
    int idx[MAX_CLIENTS + 2];
    int n = 0;
    p[n].fd = listen_fd;
    p[n].events = POLLIN;
    idx[n++] = -1;
    p[n].fd = wake_pipe[0];
    p[n].events = POLLIN;
    idx[n++] = -2;
    for (int i = 0; i < MAX_CLIENTS; i++) {
        if (clients[i].fd < 0)
            continue;
        p[n].fd = clients[i].fd;
        p[n].events = POLLIN | (clients[i].out_len ? POLLOUT : 0);
        idx[n++] = i;
    }
    int r = poll(p, (nfds_t)n, timeout_ms);
    if (r < 0)
        return;
    if (p[1].revents & POLLIN) {
        char buf[256];
        while (read(wake_pipe[0], buf, sizeof buf) > 0) {
        }
    }
    dispatch_queue();
    if (p[0].revents & POLLIN) {
        int fd = accept4(listen_fd, NULL, NULL, SOCK_NONBLOCK | SOCK_CLOEXEC);
        if (fd >= 0) {
            int slot = -1;
            for (int i = 0; i < MAX_CLIENTS; i++)
                if (clients[i].fd < 0) {
                    slot = i;
                    break;
                }
            if (slot < 0) {
                close(fd);
            } else {
                memset(&clients[slot], 0, sizeof clients[slot]);
                clients[slot].fd = fd;
                int sz = 1 << 20;
                setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &sz, sizeof sz);
                setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &sz, sizeof sz);
                LOGI("ipc: client %d connected", slot);
                on_msg(slot, 0, NULL, 0);    /* type 0: new client */
            }
        }
    }
    for (int k = 2; k < n; k++) {
        int i = idx[k];
        if (clients[i].fd < 0)
            continue;
        if (p[k].revents & (POLLIN | POLLHUP | POLLERR))
            handle_input(i);
        if (clients[i].fd < 0)
            continue;
        if (clients[i].out_len && (p[k].revents & POLLOUT)) {
            ssize_t w = write(clients[i].fd, clients[i].out, clients[i].out_len);
            if (w > 0) {
                memmove(clients[i].out, clients[i].out + w, clients[i].out_len - (size_t)w);
                clients[i].out_len -= (size_t)w;
            } else if (w < 0 && errno != EAGAIN && errno != EINTR) {
                drop_client(i);
            }
        }
    }
    /* messages queued by the handlers: try to send right away */
    dispatch_queue();
    for (int i = 0; i < MAX_CLIENTS; i++) {
        client_t *c = &clients[i];
        if (c->fd < 0 || !c->out_len)
            continue;
        ssize_t w = write(c->fd, c->out, c->out_len);
        if (w > 0) {
            memmove(c->out, c->out + w, c->out_len - (size_t)w);
            c->out_len -= (size_t)w;
        }
    }
}
