#include "batch_parse.h"

#include <algorithm>

namespace sultan {

BatchHandle::BatchHandle(std::vector<std::string> paths)
    : paths_(std::move(paths)) {
    size_t n = paths_.size();
    result_.docs.resize(n);
    result_.errors.resize(n);

    thread_ = std::thread(&BatchHandle::run, this);
}

BatchHandle::~BatchHandle() {
    if (!joined_ && thread_.joinable()) {
        thread_.join();
    }
}

BatchResult BatchHandle::result() {
    wait();
    return std::move(result_);
}

void BatchHandle::wait() {
    if (!joined_ && thread_.joinable()) {
        thread_.join();
        joined_ = true;
    }
}

JsonDoc BatchHandle::take_doc(size_t index) {
    wait();
    if (index >= result_.docs.size())
        throw std::out_of_range("BatchHandle::take_doc: index out of range");
    return std::move(result_.docs[index]);
}

const std::string& BatchHandle::error(size_t index) const {
    if (index >= result_.errors.size())
        throw std::out_of_range("BatchHandle::error: index out of range");
    return result_.errors[index].message;
}

size_t BatchHandle::error_line(size_t index) const {
    if (index >= result_.errors.size())
        throw std::out_of_range("BatchHandle::error_line: index out of range");
    return result_.errors[index].line;
}

void BatchHandle::run() {
    size_t total = paths_.size();
    if (total == 0) {
        done_.store(true, std::memory_order_release);
        return;
    }

    int n = static_cast<int>(std::thread::hardware_concurrency());
    if (n <= 0) n = 4;
    n = std::min(n, static_cast<int>(total));

    std::atomic<size_t> next_idx{0};

    auto worker = [&]() {
        while (true) {
            auto idx = next_idx.fetch_add(1, std::memory_order_relaxed);
            if (idx >= total) break;
            try {
                result_.docs[idx] = JsonDoc::parse_file(paths_[idx], true);
            } catch (const JsonParseError& e) {
                result_.errors[idx] = {e.detail, e.line};
            } catch (const std::exception& e) {
                result_.errors[idx] = {e.what(), 0};
            }
            completed_.fetch_add(1, std::memory_order_release);
        }
    };

    std::vector<std::thread> threads;
    threads.reserve(n);
    for (int i = 0; i < n; ++i) {
        threads.emplace_back(worker);
    }
    for (auto& t : threads) {
        t.join();
    }

    done_.store(true, std::memory_order_release);
}

BatchHandle* JsonDoc::batch_parse_files(
    const std::vector<std::string>& paths, bool async) {
    auto* handle = new BatchHandle(paths);
    if (!async) handle->wait();
    return handle;
}

}  // namespace sultan
