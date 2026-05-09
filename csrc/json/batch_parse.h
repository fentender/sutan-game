#pragma once
#include "json_doc.h"
#include <atomic>
#include <memory>
#include <string>
#include <thread>
#include <vector>

namespace sultan {

struct BatchResult {
    std::vector<JsonDoc> docs;
    std::vector<std::string> errors;
};

class BatchHandle {
public:
    explicit BatchHandle(std::vector<std::string> paths);
    ~BatchHandle();

    BatchHandle(const BatchHandle&) = delete;
    BatchHandle& operator=(const BatchHandle&) = delete;
    BatchHandle(BatchHandle&&) = delete;
    BatchHandle& operator=(BatchHandle&&) = delete;

    size_t total() const { return paths_.size(); }
    size_t completed() const { return completed_.load(std::memory_order_relaxed); }
    bool done() const { return done_.load(std::memory_order_acquire); }

    void wait();
    JsonDoc take_doc(size_t index);
    const std::string& error(size_t index) const;
    BatchResult result();

private:
    std::vector<std::string> paths_;
    std::atomic<size_t> completed_{0};
    std::atomic<bool> done_{false};
    BatchResult result_;
    std::thread thread_;
    bool joined_ = false;

    void run();
};

BatchResult batch_parse_files(const std::vector<std::string>& paths);

}  // namespace sultan
