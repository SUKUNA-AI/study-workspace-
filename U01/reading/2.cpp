//
// Created by sukuna on 11/6/26.
//
import std;

void classifyNumber(int number) {
    if (number < 0) {
        std::println("{} is Negative", number);
    } else if (number == 0) {
        std::println("{} is Zero", number);
    } else {
        std::println("{} is Positive", number);
    }
}

int main() {
    classifyNumber(-5);
    classifyNumber(0);
    classifyNumber(10);
    return 0;
}
